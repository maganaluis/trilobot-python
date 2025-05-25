
#!/usr/bin/env python3

import time
import logging
import sys
import os

# --- Add System Packages Path (Crucial for mise/venv) ---
SYSTEM_PACKAGES_PATH = '/usr/lib/python3/dist-packages'
if os.path.isdir(SYSTEM_PACKAGES_PATH) and SYSTEM_PACKAGES_PATH not in sys.path:
    print(f"Attempting to add system packages path: {SYSTEM_PACKAGES_PATH}")
    sys.path.append(SYSTEM_PACKAGES_PATH)
# ----------------------------------------------------

# --- Configuration ---
LOG_LEVEL = logging.INFO
TCP_SERVER_PORT = 9000
UDP_VIDEO_PORT = 9001

# --- Logging Setup ---
# Setup logging for this main script
logging.basicConfig(level=LOG_LEVEL, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Main Application ---
def main():
    """Initializes and runs the main robot application."""

    # Import modules here to allow logging setup first
    try:
        from trilobot import Trilobot
        from video_udp_streamer import VideoStreamer
        from command_tcp_server import CommandServer
    except ImportError as e:
        logging.critical(f"Failed to import necessary modules: {e}")
        logging.critical("Please ensure trilobot_hw.py, video_udp_streamer.py, and command_tcp_server.py are present.")
        sys.exit(1) # Exit if core modules are missing

    tbot = None
    streamer = None
    server = None

    try:
        # --- 1. Initialize Hardware ---
        logging.info("Initializing Trilobot Hardware...")
        tbot = Trilobot()
        # Optional: Perform a small hardware check (e.g., blink an LED)
        tbot.set_button_led(0, 0.5)
        time.sleep(0.2)
        tbot.set_button_led(0, 0)
        logging.info("Trilobot Hardware Initialized.")

        # --- 2. Initialize Video Streamer ---
        logging.info("Initializing Video Streamer...")
        streamer = VideoStreamer()
        logging.info("Video Streamer Initialized.")

        # --- 3. Initialize & Start Command Server ---
        logging.info("Initializing Command Server...")
        server = CommandServer(tbot, streamer, tcp_port=TCP_SERVER_PORT, udp_port=UDP_VIDEO_PORT)
        server.start()
        logging.info(f"Command Server started. Listening on TCP port {TCP_SERVER_PORT}.")
        logging.info(f"Video will stream via UDP on port {UDP_VIDEO_PORT} once a client connects.")

        # --- 4. Keep Running ---
        logging.info("Robot application running. Press Ctrl+C to stop.")
        while True:
            # The main thread can perform other tasks here, or just sleep.
            # For example, it could monitor battery levels, run an
            # obstacle avoidance routine when not under direct control,
            # or simply keep the program alive.
            time.sleep(10) # Sleep for a while, threads are doing the work

    except KeyboardInterrupt:
        logging.info("\nCtrl+C detected. Initiating shutdown sequence...")

    except Exception as e:
        logging.critical(f"An unexpected error occurred: {e}", exc_info=True)

    finally:
        # --- 5. Clean Shutdown ---
        logging.info("Starting shutdown...")

        if server:
            logging.info("Stopping Command Server...")
            server.stop()

        # Although server.stop() should stop the streamer,
        # calling close() ensures camera and socket are released.
        if streamer:
            logging.info("Closing Video Streamer resources...")
            streamer.close()

        if tbot:
            logging.info("Stopping Trilobot Motors and cleaning up GPIO...")
            tbot.stop() # Ensure motors are stopped
            # GPIO cleanup should happen in Trilobot's __del__

        logging.info("Robot shutdown complete.")

# --- Run the Main Application ---
if __name__ == "__main__":
    main()
