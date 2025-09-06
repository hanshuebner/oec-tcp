import re
import sys
import os
import signal
import logging
import threading
import socket
from concurrent.futures import ThreadPoolExecutor

from coax import open_tcp_interface, TerminalType, TcpInterface

from .args import parse_args
from .interface import InterfaceWrapper
from .controller import Controller
from .device import get_ids, get_features, get_keyboard_description, UnsupportedDeviceError
from .terminal import Terminal
from .tn3270 import TN3270Session


from .keymap_3278_typewriter import KEYMAP as KEYMAP_3278_TYPEWRITER
from .keymap_ibm_typewriter import KEYMAP as KEYMAP_IBM_TYPEWRITER
from .keymap_ibm_enhanced import KEYMAP as KEYMAP_IBM_ENHANCED

logging.basicConfig(level=logging.getLevelName(os.getenv("OEC_LOG_LEVEL", "INFO")))

logger = logging.getLogger('oec.main')

# Global variables for managing multiple connections
active_controllers = []
controller_executor = None
shutdown_event = threading.Event()

class TcpServer:
    """TCP server for accepting 3270 coax connections."""

    def __init__(self, host="0.0.0.0", port=3174):
        self.host = host
        self.port = port
        self.server_socket = None
        self.running = False
        self.server_thread = None

    def start(self, connection_callback):
        """Start the TCP server to accept incoming connections."""
        if self.server_socket is not None:
            return  # Already running

        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_socket.bind((self.host, self.port))
        self.server_socket.listen(1)
        self.server_socket.settimeout(1.0)  # 1 second timeout for accept

        self.running = True
        self.connection_callback = connection_callback
        self.server_thread = threading.Thread(target=self._accept_connections, daemon=True)
        self.server_thread.start()

    def stop(self):
        """Stop the TCP server."""
        self.running = False

        if self.server_socket:
            self.server_socket.close()
            self.server_socket = None

    def _accept_connections(self):
        """Accept incoming connections."""
        while self.running:
            try:
                client_socket, client_address = self.server_socket.accept()
                client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                logger.info(f"Client connected from {client_address}")

                # Call the callback to handle the connection
                if self.connection_callback:
                    self.connection_callback(client_socket, client_address)

            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    logger.error(f"Error accepting connection: {e}")
                break

def _get_keymap(_args, keyboard_description):
    if keyboard_description.startswith('3278'):
        return KEYMAP_3278_TYPEWRITER

    if keyboard_description.startswith('IBM-TYPEWRITER'):
        return KEYMAP_IBM_TYPEWRITER

    if keyboard_description.startswith('IBM-ENHANCED'):
        return KEYMAP_IBM_ENHANCED

    return KEYMAP_3278_TYPEWRITER

def _create_device(args, interface, _poll_response):
    # Read the terminal identifiers.
    (terminal_id, extended_id) = get_ids(interface)

    logger.info(f'Terminal ID = {terminal_id}')

    if terminal_id.type != TerminalType.CUT:
        raise UnsupportedDeviceError('Only CUT type terminals are supported')

    logger.info(f'Extended ID = {extended_id}')

    if extended_id is not None:
        logger.info(f'Model = IBM {extended_id[2:6]} or equivalent')

    keyboard_description = get_keyboard_description(terminal_id, extended_id)

    logger.info(f'Keyboard = {keyboard_description}')

    # Read the terminal features.
    features = get_features(interface)

    logger.info(f'Features = {features}')

    # Get the keymap.
    keymap = _get_keymap(args, keyboard_description)

    logger.info(f'Keymap = {keymap.name}')

    # Create the terminal.
    terminal = Terminal(interface, terminal_id, extended_id, features, keymap)

    return terminal

def _create_session(args, device):
    return TN3270Session(device, args.host, args.port, args.device_names, args.character_encoding, args.tn3270e_profile)

def _handle_new_connection(client_socket, client_address, args):
    """Handle a new client connection by creating a controller in a separate thread."""
    logger.info(f'Handling new connection from {client_address}')

    try:
        # Create a new interface instance for this client using the context manager
        with open_tcp_interface(client_socket) as client_interface:
            interface_wrapper = InterfaceWrapper(client_interface)

            def create_device(interface, poll_response):
                return _create_device(args, interface, poll_response)

            def create_session(device):
                return _create_session(args, device)

            # Create and run the controller
            controller = Controller(interface_wrapper, create_device, create_session)

            # Add to active controllers list
            active_controllers.append(controller)

            try:
                controller.run()
            finally:
                # Remove from active controllers when done
                if controller in active_controllers:
                    active_controllers.remove(controller)
                logger.info(f'Controller for {client_address} finished')

    except Exception as e:
        logger.error(f'Error handling connection from {client_address}: {e}')
        try:
            client_socket.close()
        except:
            pass

def main():
    global controller_executor, args

    args = parse_args(sys.argv[1:])

    def create_device(interface, poll_response):
        return _create_device(args, interface, poll_response)

    def create_session(device):
        return _create_session(args, device)

    def connection_callback(client_socket, client_address):
        """Callback function called when a new client connects."""
        # Submit the connection handling to the thread pool
        if controller_executor and not shutdown_event.is_set():
            future = controller_executor.submit(_handle_new_connection, client_socket, client_address, args)
            # Store the future to track it if needed
            # Note: We don't store futures here to avoid memory leaks from completed tasks

    logger.info('Starting multi-connection server...')

    if not re.match(r'^tcp://.*(|:\d+)$', args.interface):
        raise ValueError(f'Only TCP interfaces are supported. Expected format: tcp://host:port, got: {args.interface}')

    interface_spec = re.sub(r'^tcp://', '', args.interface)

    # Initialize thread pool for handling connections
    controller_executor = ThreadPoolExecutor(max_workers=10, thread_name_prefix="Controller")

    def signal_handler(_number, _frame):
        logger.info('Shutting down server...')
        shutdown_event.set()

        # Stop all active controllers
        for controller in active_controllers[:]:  # Copy list to avoid modification during iteration
            try:
                controller.stop()
            except Exception as e:
                logger.error(f'Error stopping controller: {e}')

        # Shutdown thread pool
        if controller_executor:
            controller_executor.shutdown(wait=True)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Parse host and port from interface spec
    if ':' in interface_spec:
        host, port_str = interface_spec.rsplit(':', 1)
        port = int(port_str)
    else:
        host = interface_spec
        port = 3174  # Default port

    # Create and start the TCP server
    server = TcpServer(host, port)

    try:
        server.start(connection_callback)
        logger.info(f'Server listening on {host}:{port}, waiting for connections...')

        # Keep the main thread alive while the server runs
        while not shutdown_event.is_set():
            try:
                shutdown_event.wait(1.0)  # Wait 1 second or until shutdown
            except KeyboardInterrupt:
                break

    except Exception as e:
        logger.error(f'Server error: {e}')
    finally:
        # Cleanup
        server.stop()
        shutdown_event.set()
        if controller_executor:
            controller_executor.shutdown(wait=True)
        logger.info('Server stopped')

if __name__ == '__main__':
    main()
