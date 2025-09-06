import re
import sys
import os
import signal
import logging

from coax import open_tcp_interface, TerminalType

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

def main():
    args = parse_args(sys.argv[1:])

    def create_device(interface, poll_response):
        return _create_device(args, interface, poll_response)

    def create_session(device):
        return _create_session(args, device)

    logger.info('Starting controller...')

    if not re.match(r'^tcp://.*(|:\d+)$', args.interface):
        raise ValueError(f'Only TCP interfaces are supported. Expected format: tcp://host:port, got: {args.interface}')

    interface_opener = open_tcp_interface
    interface_spec = re.sub(r'^tcp://', '', args.interface)

    with interface_opener(interface_spec) as interface:
        # For TCP interfaces, wait for a client connection before starting
        if interface_opener == open_tcp_interface:
            logger.info(f'Waiting for client connection on {interface_spec}...')
            interface.wait_for_connection()  # Wait indefinitely for connection
            logger.info('Client connected, starting controller...')

        controller = Controller(InterfaceWrapper(interface), create_device, create_session)

        def signal_handler(_number, _frame):
            logger.info('Stopping controller...')

            controller.stop()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        controller.run()

if __name__ == '__main__':
    main()
