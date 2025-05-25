#!/usr/bin/env python3

import socket
import threading
import json
import logging
import time
import numpy as np
import cv2 # OpenCV for video display

# --- Configuration ---
LOG_LEVEL = logging.INFO
UDP_BUFFER_SIZE = 65536 # Large enough for most JPEGs
TCP_TIMEOUT = 5.0       # Seconds for TCP connection attempt
UDP_TIMEOUT = 1.0       # Seconds for UDP receive timeout

# --- Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Robot Client Class ---
class RobotClient:
    """
    Connects to the robot, sends commands, and receives video.
    """
    def __init__(self, robot_ip: str, tcp_port: int = 9000, udp_port: int = 9001):
        self.robot_ip = robot_ip
        self.tcp_port = tcp_port
        self.udp_port = udp_port

        self.tcp_socket = None
        self.udp_socket = None

        self.is_connected = False
        self._running = False

        self._video_frame = None
        self._video_lock = threading.Lock()

        self._udp_thread = None
        self._tcp_recv_thread = None # To handle potential responses

    def connect(self) -> bool:
        """Establishes TCP and UDP connections."""
        if self.is_connected:
            logging.warning("Already connected.")
            return True

        logging.info(f"Attempting to connect to {self.robot_ip}...")
        try:
            # --- TCP Connection ---
            logging.debug(f"Connecting TCP to {self.robot_ip}:{self.tcp_port}...")
            self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.tcp_socket.settimeout(TCP_TIMEOUT)
            self.tcp_socket.connect((self.robot_ip, self.tcp_port))
            self.tcp_socket.settimeout(None) # Remove timeout after connection
            logging.info("TCP Connected.")

            # --- UDP Setup ---
            logging.debug(f"Setting up UDP listener on port {self.udp_port}...")
            self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.udp_socket.bind(('', self.udp_port)) # Listen on all interfaces
            self.udp_socket.settimeout(UDP_TIMEOUT)
            logging.info(f"UDP Listening on port {self.udp_port}.")

            self.is_connected = True
            self._running = True

            # --- Start Listener Threads ---
            self._udp_thread = threading.Thread(target=self._udp_listen_worker, daemon=True, name="UDPClientThread")
            self._udp_thread.start()
            self._tcp_recv_thread = threading.Thread(target=self._tcp_listen_worker, daemon=True, name="TCPClientRecvThread")
            self._tcp_recv_thread.start()

            return True

        except socket.timeout:
            logging.error(f"Connection timed out ({TCP_TIMEOUT}s). Is robot running and IP {self.robot_ip} correct?")
            self._cleanup_sockets()
            return False
        except ConnectionRefusedError:
            logging.error(f"Connection refused. Is the robot's CommandServer running on port {self.tcp_port}?")
            self._cleanup_sockets()
            return False
        except Exception as e:
            logging.error(f"Failed to connect: {e}", exc_info=True)
            self._cleanup_sockets()
            return False

    def _cleanup_sockets(self):
        """Closes sockets if they exist."""
        if self.tcp_socket:
            self.tcp_socket.close()
            self.tcp_socket = None
        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None
        self.is_connected = False

    def _udp_listen_worker(self):
        """Listens for incoming UDP video frames."""
        logging.info("UDP listener thread started.")
        while self._running:
            try:
                data, addr = self.udp_socket.recvfrom(UDP_BUFFER_SIZE)
                if addr[0] == self.robot_ip: # Only accept video from our robot
                    with self._video_lock:
                        self._video_frame = data
            except socket.timeout:
                continue # Just check self._running again
            except Exception as e:
                if self._running: # Only log if we weren't expecting a close
                    logging.error(f"UDP receive error: {e}")
                break
        logging.info("UDP listener thread stopped.")

    def _tcp_listen_worker(self):
        """Listens for any incoming TCP messages (acks, sensor data)."""
        logging.info("TCP listener thread started.")
        buffer = ""
        try:
            while self._running:
                data = self.tcp_socket.recv(1024)
                if not data:
                    logging.warning("TCP connection closed by server.")
                    self.disconnect() # Initiate disconnect if server closes
                    break

                buffer += data.decode('utf-8', errors='ignore')
                while '\n' in buffer:
                    line, buffer = buffer.split('\n', 1)
                    if line:
                        logging.info(f"Robot says: {line}")
                        # You can add JSON parsing here if you expect structured responses
        except ConnectionAbortedError:
             logging.warning("TCP connection aborted.")
        except Exception as e:
            if self._running:
                logging.error(f"TCP receive error: {e}")
        finally:
            logging.info("TCP listener thread stopped.")
            if self._running: # If we stopped due to error, initiate full disconnect
                 self.disconnect()


    def get_latest_frame(self):
        """Gets the latest video frame and decodes it using OpenCV."""
        frame_data = None
        with self._video_lock:
            frame_data = self._video_frame

        if frame_data:
            try:
                np_arr = np.frombuffer(frame_data, np.uint8)
                frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
                return frame
            except Exception as e:
                logging.warning(f"Failed to decode JPEG frame: {e}")
                return None
        return None

    def send_command(self, action: str, **kwargs):
        """Sends a command as JSON over TCP."""
        if not self.is_connected or not self.tcp_socket:
            logging.error("Not connected. Cannot send command.")
            return

        try:
            command = {'action': action, **kwargs}
            json_str = json.dumps(command) + '\n' # Add newline as delimiter
            self.tcp_socket.sendall(json_str.encode('utf-8'))
            logging.debug(f"Sent: {json_str.strip()}")
        except BrokenPipeError:
            logging.error("Connection broken. Attempting to disconnect.")
            self.disconnect()
        except Exception as e:
            logging.error(f"Failed to send command: {e}")
            self.disconnect() # Disconnect on error

    def disconnect(self):
        """Disconnects from the robot and cleans up."""
        if not self._running:
            return

        logging.info("Disconnecting...")
        self._running = False

        if self.tcp_socket:
            try:
                # Politely tell the server we're leaving (optional)
                self.send_command('exit')
                time.sleep(0.1) # Give it a moment
                self.tcp_socket.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass # Ignore errors during shutdown
            finally:
                self.tcp_socket.close()
                self.tcp_socket = None

        if self.udp_socket:
            self.udp_socket.close()
            self.udp_socket = None

        # Wait for threads to finish (optional, as they are daemons)
        # if self._udp_thread: self._udp_thread.join(timeout=1)
        # if self._tcp_recv_thread: self._tcp_recv_thread.join(timeout=1)

        self.is_connected = False
        logging.info("Disconnected.")

# --- Example Usage (Interactive Control) ---
if __name__ == "__main__":
    robot_ip = input("Enter Robot's IP address: ")
    if not robot_ip:
        print("IP address required.")
        exit(1)

    client = RobotClient(robot_ip)

    if not client.connect():
        print("Failed to connect to the robot. Exiting.")
        exit(1)

    print("\n--- Interactive Control (Hold-Style Sim) ---")
    print("  w: Forward    s: Backward")
    print("  a: Left       d: Right")
    print("  x: Explicit Stop")
    print("  (Release key or press unassigned to Stop)")
    print("  p: Ping       l: LEDs On (example)")
    print("  q: Quit")
    print("------------------------------------------")


    cv2.namedWindow("Trilobot Feed")
    last_command_sent = None # Track the last command

    try:
        while True:
            frame = client.get_latest_frame()
            if frame is not None:
                cv2.imshow("Trilobot Feed", frame)
            else:
                blank_frame = np.zeros((480, 640, 3), dtype=np.uint8)
                cv2.putText(blank_frame, "Waiting for Video...", (180, 240), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
                cv2.imshow("Trilobot Feed", blank_frame)

            key = cv2.waitKey(50) & 0xFF # Wait 50ms for a key

            command_to_send = None

            if key == ord('w'):
                command_to_send = {'action': 'forward', 'speed': 2.0}
            elif key == ord('s'):
                command_to_send = {'action': 'backward', 'speed': 0.8}
            elif key == ord('a'):
                command_to_send = {'action': 'turn_left', 'speed': 0.7}
            elif key == ord('d'):
                command_to_send = {'action': 'turn_right', 'speed': 0.7}
            elif key == ord('x'):
                command_to_send = {'action': 'stop'}
            elif key == ord('p'):
                command_to_send = {'action': 'ping'}
            elif key == ord('l'):
                 command_to_send = {'action': 'fill_underlighting', 'r': 0, 'g': 0, 'b': 200}
            elif key == ord('q'):
                break # Exit loop
            elif key != 0xFF: # 0xFF (or -1 depending on OS/version) means no key pressed
                command_to_send = {'action': 'stop'} # Stop if an *unassigned* key is pressed
            else: # No key was pressed (key == 0xFF or -1)
                command_to_send = {'action': 'stop'}

            # Only send if the command is new
            if command_to_send and command_to_send != last_command_sent:
                client.send_command(**command_to_send)
                last_command_sent = command_to_send
            # If no new command, but the last one was movement, AND no key was pressed, send stop.
            # This ensures it stops when you release a key.
            elif not command_to_send and last_command_sent and last_command_sent.get('action') != 'stop':
                 client.send_command(action='stop')
                 last_command_sent = {'action': 'stop'}


    finally:
        print("Shutting down client...")
        if client.is_connected:
            client.send_command('stop') # Ensure robot stops before disconnecting
            time.sleep(0.1)
            client.disconnect()
        cv2.destroyAllWindows()
        print("Client finished.")
