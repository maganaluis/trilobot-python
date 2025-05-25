#!/usr/bin/env python3

import io
import time
import logging
import socket
import threading

try:
    from picamera2 import Picamera2
    from picamera2.encoders import JpegEncoder
    from picamera2.outputs import FileOutput
except ImportError:
    print("Error: picamera2 library not found.")
    print("Please ensure it is installed and accessible.")
    # You might want to fall back to a dummy class or exit if essential
    Picamera2 = None # Set to None to handle absence later

# --- Configuration ---
LOG_LEVEL = logging.INFO
MAX_UDP_PACKET_SIZE = 65507  # Max theoretical UDP payload size

# --- Logging Setup ---
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(threadName)s - %(message)s')

# --- Streaming Output Buffer ---
class StreamingOutput(io.BufferedIOBase):
    """
    A buffer class that holds the latest captured frame and uses a
    Condition to notify waiting threads when a new frame is available.
    """
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

# --- Video Streamer Class ---
class VideoStreamer:
    """
    Manages camera capture and streams video frames over UDP.
    """
    def __init__(self, resolution=(640, 480), framerate=24, jpeg_quality=85):
        if Picamera2 is None:
            raise RuntimeError("Picamera2 library is not available.")

        self.picam2 = Picamera2()
        self.resolution = resolution
        self.framerate = framerate
        self.jpeg_quality = jpeg_quality

        # Configure camera
        config = self.picam2.create_video_configuration(
            main={"size": self.resolution},
            controls={"FrameRate": float(self.framerate)} # Ensure it's float
        )
        self.picam2.configure(config)

        self.output = StreamingOutput()
        self.encoder = JpegEncoder(q=self.jpeg_quality)
        self.udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.target_ip = None
        self.target_port = None
        self.running = False
        self._stream_thread = None
        logging.info("VideoStreamer initialized.")

    def _stream_worker(self):
        """Worker function that runs in its own thread."""
        logging.info("Starting video recording and streaming worker...")
        self.picam2.start_recording(self.encoder, FileOutput(self.output))

        while self.running:
            with self.output.condition:
                # Wait for a new frame or until notified to stop
                self.output.condition.wait()
                if not self.running:  # Check again after waking up
                    break
                frame = self.output.frame

            if frame and self.target_ip and self.target_port:
                if len(frame) > MAX_UDP_PACKET_SIZE:
                    logging.warning(f"Frame size ({len(frame)}) exceeds max UDP size ({MAX_UDP_PACKET_SIZE}). Skipping.")
                    continue # Skip this frame

                try:
                    self.udp_socket.sendto(frame, (self.target_ip, self.target_port))
                except OSError as e:
                    logging.error(f"UDP send error: {e}")
                    # Consider stopping or pausing if errors persist
                except Exception as e:
                    logging.error(f"Unexpected error sending UDP frame: {e}")

        logging.info("Stopping video recording...")
        self.picam2.stop_recording()
        logging.info("Streaming worker finished.")

    def start(self, target_ip, target_port):
        """Starts the video streaming thread."""
        if self.running:
            logging.warning("Streamer is already running.")
            return

        if not target_ip or not target_port:
            logging.error("Target IP and Port must be set to start streaming.")
            return

        self.target_ip = target_ip
        self.target_port = int(target_port) # Ensure port is integer
        self.running = True

        self._stream_thread = threading.Thread(target=self._stream_worker, name="UDPStreamThread", daemon=True)
        self._stream_thread.start()
        logging.info(f"Video streaming started, sending to {self.target_ip}:{self.target_port}")

    def stop(self):
        """Stops the video streaming thread."""
        if not self.running:
            logging.warning("Streamer is not running.")
            return

        logging.info("Stopping video streamer...")
        self.running = False

        # Notify the waiting thread to wake up and check the 'running' flag
        with self.output.condition:
            self.output.condition.notify_all()

        if self._stream_thread:
            self._stream_thread.join(timeout=5) # Wait for thread to finish
            if self._stream_thread.is_alive():
                logging.warning("Streaming thread did not stop gracefully.")

        self.target_ip = None
        self.target_port = None
        logging.info("Video streamer stopped.")

    def close(self):
        """Releases all resources."""
        self.stop() # Ensure it's stopped
        self.udp_socket.close()
        self.picam2.close()
        logging.info("VideoStreamer resources released.")

    def __del__(self):
        """Attempt to clean up resources when the object is deleted."""
        self.close()


# --- Self-Test Block ---
if __name__ == "__main__":
    print("--- Testing VideoStreamer Module ---")
    TEST_IP = '127.0.0.1'  # Send to localhost for testing
    TEST_PORT = 9001
    TEST_DURATION = 15     # Run test for 15 seconds

    # --- Simple UDP Receiver (for testing) ---
    receiver_running = True
    frame_count = 0
    def udp_receiver(port):
        global frame_count
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.bind(('', port))
            print(f"Test receiver listening on port {port}...")
            sock.settimeout(1.0) # Set timeout to check running flag
            while receiver_running:
                try:
                    data, addr = sock.recvfrom(MAX_UDP_PACKET_SIZE + 100)
                    frame_count += 1
                    print(f"\rReceived frame {frame_count} ({len(data)} bytes)", end="")
                except socket.timeout:
                    continue # Just check the running flag again
        except Exception as e:
            print(f"\nReceiver error: {e}")
        finally:
            print("\nReceiver closing.")
            sock.close()

    # Start the receiver in its own thread
    receiver_thread = threading.Thread(target=udp_receiver, args=(TEST_PORT,), daemon=True)
    receiver_thread.start()
    time.sleep(1) # Give receiver a moment to start

    # --- Start the Streamer ---
    try:
        streamer = VideoStreamer()
        streamer.start(TEST_IP, TEST_PORT)

        print(f"Streaming to {TEST_IP}:{TEST_PORT} for {TEST_DURATION} seconds...")
        time.sleep(TEST_DURATION)

    except RuntimeError as e:
        print(f"Failed to start streamer: {e}")
    except KeyboardInterrupt:
        print("\nTest interrupted by user.")
    finally:
        print("\n--- Stopping Test ---")
        if 'streamer' in locals() and streamer.running:
            streamer.stop()
            streamer.close()

        receiver_running = False
        receiver_thread.join(timeout=2)
        print("Test finished.")
