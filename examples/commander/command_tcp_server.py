#!/usr/bin/env python3

import socket
import threading
import json
import logging
import time

# Assuming these modules exist in the same directory or are importable
from trilobot import Trilobot  # Your hardware class
from video_udp_streamer import VideoStreamer

# --- Configuration ---
LOG_LEVEL = logging.INFO
TCP_HOST = '0.0.0.0'  # Listen on all available interfaces
TCP_PORT = 9000       # Port for incoming control commands
UDP_PORT = 9001       # Port to send video to (client listens here)
BUFFER_SIZE = 1024    # TCP receive buffer size

# --- Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

# --- Command Server Class ---
class CommandServer:
    """
    Listens for TCP connections, handles commands, and manages video streaming.
    """
    def __init__(self, tbot: Trilobot, video_streamer: VideoStreamer, host='0.0.0.0', tcp_port=9000, udp_port=9001):
        """
        Initializes the CommandServer.

        Args:
            tbot: An instance of the Trilobot hardware class.
            video_streamer: An instance of the VideoStreamer class.
            host (str): The host IP address to listen on.
            tcp_port (int): The TCP port for commands.
            udp_port (int): The UDP port where video should be sent.
        """
        self.tbot = tbot
        self.video_streamer = video_streamer
        self.host = host
        self.tcp_port = tcp_port  # <-- Make sure this is here
        self.udp_port = udp_port  # <-- Make sure this is here

        self.tcp_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.tcp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        self.running = False  # <-- Make sure this is here
        self._listen_thread = None
        self._client_handler_thread = None
        self._client_lock = threading.Lock()
        logging.info("CommandServer initialized.") # Added for clarity

    def _parse_command(self, command_data: str, conn: socket.socket) -> bool:
        """Parses a JSON command and executes it. Returns False if client should disconnect."""
        try:
            command = json.loads(command_data)
            action = command.get('action', '').lower()
            logging.debug(f"Received command: {command}")

            if not action:
                logging.warning("Received command without 'action'.")
                return True # Continue listening

            # --- Motion Commands ---
            if action == 'forward':
                speed = float(command.get('speed', 1.0))
                logging.info(f"Got forward command")
                self.tbot.forward(speed)
            elif action == 'backward':
                speed = float(command.get('speed', 1.0))
                self.tbot.backward(speed)
            elif action == 'turn_left':
                speed = float(command.get('speed', 1.0))
                self.tbot.turn_left(speed)
            elif action == 'turn_right':
                speed = float(command.get('speed', 1.0))
                self.tbot.turn_right(speed)
            elif action == 'set_speeds':
                left = float(command.get('left', 0.0))
                right = float(command.get('right', 0.0))
                self.tbot.set_motor_speeds(left, right)
            elif action == 'stop':
                self.tbot.stop()
            elif action == 'coast':
                self.tbot.coast()

            # --- LED Commands (Example) ---
            elif action == 'set_led':
                led_id = int(command.get('led'))
                value = float(command.get('value'))
                self.tbot.set_button_led(led_id, value)
            elif action == 'fill_underlighting':
                r = int(command.get('r', 0))
                g = int(command.get('g', 0))
                b = int(command.get('b', 0))
                self.tbot.fill_underlighting(r, g, b)

            # --- Sensor Commands (Example) ---
            elif action == 'read_distance':
                distance = self.tbot.read_distance()
                response = json.dumps({'status': 'distance', 'value': distance}) + '\n'
                conn.sendall(response.encode('utf-8'))

            # --- Control Commands ---
            elif action == 'ping':
                conn.sendall(b'{"status": "pong"}\n')
            elif action == 'exit':
                logging.info("Client requested exit.")
                return False # Signal to disconnect

            else:
                logging.warning(f"Unknown action received: {action}")

            return True # Continue listening

        except json.JSONDecodeError:
            logging.error(f"Failed to decode JSON: {command_data}")
            return True # Continue, maybe client will send valid JSON
        except (ValueError, TypeError, KeyError) as e:
            logging.error(f"Error processing command {command_data}: {e}")
            return True # Continue, bad command format
        except Exception as e:
            logging.critical(f"Unexpected error handling command {command_data}: {e}")
            return False # Something bad happened, disconnect

    def _handle_client(self, conn: socket.socket, addr):
        """Handles a single client connection."""
        client_ip, client_port = addr
        logging.info(f"Client connected: {client_ip}:{client_port}")

        # Start video stream to this client
        self.video_streamer.start(client_ip, self.udp_port)

        try:
            # Use makefile for easier line-based reading
            client_file = conn.makefile('r', encoding='utf-8', buffering=1) # Line buffered

            for line in client_file:
                line = line.strip()
                if not line:
                    continue # Skip empty lines

                if not self._parse_command(line, conn):
                    break # Command indicated disconnection or error

        except ConnectionResetError:
            logging.warning(f"Client {client_ip}:{client_port} reset the connection.")
        except Exception as e:
            logging.error(f"Error handling client {client_ip}:{client_port}: {e}")
        finally:
            logging.info(f"Client disconnected: {client_ip}:{client_port}")
            self.video_streamer.stop() # Stop video stream
            conn.close()
            self._client_handler_thread = None # Mark as finished

    def _listen_worker(self):
        """Listens for incoming TCP connections."""
        self.tcp_socket.bind((self.host, self.tcp_port))
        self.tcp_socket.listen(1) # Listen for one connection
        logging.info(f"TCP Server listening on {self.host}:{self.tcp_port}")

        while self.running:
            try:
                logging.debug("Waiting for a client connection...")
                conn, addr = self.tcp_socket.accept()

                if not self.running: # Check if stop was called while waiting
                    conn.close()
                    break

                # Ensure only one client is handled at a time
                with self._client_lock:
                    if self._client_handler_thread and self._client_handler_thread.is_alive():
                        logging.warning(f"Another client {addr} tried to connect. Rejecting.")
                        conn.sendall(b'{"error": "Server busy"}\n')
                        conn.close()
                    else:
                        # Start a thread to handle this client
                        self._client_handler_thread = threading.Thread(
                            target=self._handle_client,
                            args=(conn, addr),
                            name="TCPClientHandler",
                            daemon=True
                        )
                        self._client_handler_thread.start()

            except OSError as e:
                if self.running: # Only log if we weren't expecting a close
                    logging.error(f"Socket error: {e}")
                else:
                    logging.info("Socket closed for shutdown.")
                break # Exit loop on error or shutdown
            except Exception as e:
                logging.critical(f"Unexpected error in listen worker: {e}")
                time.sleep(1) # Prevent rapid-fire errors

        logging.info("Listen worker stopped.")

    def start(self):
        """Starts the TCP server listening thread."""
        if self.running:
            logging.warning("Server is already running.")
            return

        self.running = True
        self._listen_thread = threading.Thread(target=self._listen_worker, name="TCPListenThread", daemon=True)
        self._listen_thread.start()

    def stop(self):
        """Stops the TCP server."""
        if not self.running:
            logging.warning("Server is not running.")
            return

        logging.info("Stopping TCP Server...")
        self.running = False
        self.video_streamer.stop() # Ensure video stops

        # To unblock socket.accept(), we can try connecting to ourselves.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect((self.host if self.host != '0.0.0.0' else '127.0.0.1', self.tcp_port))
        except Exception:
             pass # This is expected if already closing or no connection

        self.tcp_socket.close() # Close the main socket

        if self._listen_thread:
            self._listen_thread.join(timeout=2)

        if self._client_handler_thread: # If a client was connected
            self._client_handler_thread.join(timeout=2)

        logging.info("TCP Server stopped.")

    def __del__(self):
        self.stop()


# --- Mock Classes for Testing ---
class MockTrilobot:
    """A dummy Trilobot class for testing the server without hardware."""
    def __getattr__(self, name):
        def method(*args, **kwargs):
            print(f"MOCK_TBOT: Called {name} with args={args}, kwargs={kwargs}")
            if name == 'read_distance':
                return 99.9
        return method

class MockVideoStreamer:
    """A dummy VideoStreamer class for testing."""
    def start(self, ip, port):
        print(f"MOCK_VIDEO: Started streaming to {ip}:{port}")
    def stop(self):
        print("MOCK_VIDEO: Stopped streaming.")
    def close(self):
        print("MOCK_VIDEO: Closed.")


# --- Self-Test Block ---
if __name__ == "__main__":
    print("--- Testing CommandServer Module ---")

    mock_tbot = MockTrilobot()
    mock_streamer = MockVideoStreamer()
    server = CommandServer(mock_tbot, mock_streamer)

    try:
        server.start()
        print(f"Mock server started. Connect via TCP on port {TCP_PORT}.")
        print("Send JSON commands, e.g.,:")
        print('  {"action": "forward", "speed": 0.5}')
        print('  {"action": "stop"}')
        print('  {"action": "exit"}')
        print("Press Ctrl+C to stop the test server.")
        while True:
            time.sleep(5) # Keep main thread alive
            # You could add checks here if needed

    except KeyboardInterrupt:
        print("\nCtrl+C detected, shutting down test server...")
    finally:
        server.stop()
        print("Test finished.")
