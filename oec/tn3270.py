"""
oec.tn3270
~~~~~~~~~~
"""

import logging
from tn3270 import Telnet, TN3270EFunction, Emulator, AttributeCell, CharacterCell, AID, Color, \
                   Highlight, OperatorError, ProtectedCellOperatorError, FieldOverflowOperatorError
from tn3270.ebcdic import DUP, FM

from .session import Session, SessionDisconnectedError
from .display import encode_character, encode_string
from .keyboard import Key, get_character_for_key
import time

AID_KEY_MAP = {
    Key.CLEAR: AID.CLEAR,
    Key.ENTER: AID.ENTER,
    Key.PA1: AID.PA1,
    Key.PA2: AID.PA2,
    Key.PA3: AID.PA3,
    Key.PF1: AID.PF1,
    Key.PF2: AID.PF2,
    Key.PF3: AID.PF3,
    Key.PF4: AID.PF4,
    Key.PF5: AID.PF5,
    Key.PF6: AID.PF6,
    Key.PF7: AID.PF7,
    Key.PF8: AID.PF8,
    Key.PF9: AID.PF9,
    Key.PF10: AID.PF10,
    Key.PF11: AID.PF11,
    Key.PF12: AID.PF12,
    Key.PF13: AID.PF13,
    Key.PF14: AID.PF14,
    Key.PF15: AID.PF15,
    Key.PF16: AID.PF16,
    Key.PF17: AID.PF17,
    Key.PF18: AID.PF18,
    Key.PF19: AID.PF19,
    Key.PF20: AID.PF20,
    Key.PF21: AID.PF21,
    Key.PF22: AID.PF22,
    Key.PF23: AID.PF23,
    Key.PF24: AID.PF24
}

class TN3270Session(Session):
    """TN3270 session."""

    def __init__(self, terminal, host, port, device_names, character_encoding, tn3270e_profile):
        super().__init__(terminal)

        self.logger = logging.getLogger(__name__)

        self.host = host
        self.port = port
        self.device_names = device_names
        self.character_encoding = character_encoding
        self.tn3270e_profile = tn3270e_profile

        self.telnet = None
        self.emulator = None

        self.keyboard_insert = False
        self.waiting_on_host = False
        self.operator_error = None

        # TODO: Should the message area be initialized here?
        self.message_area = None
        self.last_message_area = None

    def start(self):
        self._connect_host()

        (rows, columns) = self.terminal.display.dimensions

        if self.terminal.display.has_eab:
            supported_colors = 8
            supported_highlights = [Highlight.BLINK, Highlight.REVERSE, Highlight.UNDERSCORE]
        else:
            supported_colors = 1
            supported_highlights = []

        self.emulator = Emulator(self.telnet, rows, columns, supported_colors, supported_highlights)

        self.emulator.alarm = lambda: self.terminal.sound_alarm()

    def terminate(self):
        if self.telnet:
            self._disconnect_host()

        self.emulator = None

    def fileno(self):
        return self.emulator.stream.socket.fileno()

    def handle_host(self):
        handle_host_start = time.perf_counter()

        try:
            emulator_update_start = time.perf_counter()
            if not self.emulator.update(timeout=0):
                emulator_update_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    emulator_update_duration = (emulator_update_time - emulator_update_start) * 1000
                    self.logger.debug(f'Emulator update (no data): {emulator_update_duration:.2f}ms')
                return False
            emulator_update_time = time.perf_counter()

            if self.logger.isEnabledFor(logging.DEBUG):
                emulator_update_duration = (emulator_update_time - emulator_update_start) * 1000
                self.logger.debug(f'Emulator update (data received): {emulator_update_duration:.2f}ms')
        except (EOFError, ConnectionResetError):
            self._disconnect_host()
            raise SessionDisconnectedError

        self.waiting_on_host = False

        if self.logger.isEnabledFor(logging.DEBUG):
            total_handle_host_time = (time.perf_counter() - handle_host_start) * 1000
            self.logger.debug(f'Handle host total: {total_handle_host_time:.2f}ms')

        return True

    def handle_key(self, key, keyboard_modifiers, scan_code):
        handle_key_start = time.perf_counter()

        aid_lookup_start = time.perf_counter()
        aid = AID_KEY_MAP.get(key)
        aid_lookup_time = time.perf_counter()

        try:
            if aid is not None:
                reset_insert_start = time.perf_counter()
                self._reset_insert()
                reset_insert_time = time.perf_counter()

                aid_send_start = time.perf_counter()
                self.emulator.aid(aid)
                aid_send_time = time.perf_counter()

                self.waiting_on_host = True

                if self.logger.isEnabledFor(logging.DEBUG):
                    reset_insert_duration = (reset_insert_time - reset_insert_start) * 1000
                    aid_send_duration = (aid_send_time - aid_send_start) * 1000
                    self.logger.debug(f'AID key handling: reset_insert={reset_insert_duration:.2f}ms, aid_send={aid_send_duration:.2f}ms')
            #elif key == Key.RESET:
            elif key == Key.TAB:
                tab_start = time.perf_counter()
                self.emulator.tab()
                tab_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    tab_duration = (tab_time - tab_start) * 1000
                    self.logger.debug(f'TAB operation: {tab_duration:.2f}ms')
            elif key == Key.BACKTAB:
                backtab_start = time.perf_counter()
                self.emulator.tab(direction=-1)
                backtab_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    backtab_duration = (backtab_time - backtab_start) * 1000
                    self.logger.debug(f'BACKTAB operation: {backtab_duration:.2f}ms')
            elif key == Key.NEWLINE:
                newline_start = time.perf_counter()
                self.emulator.newline()
                newline_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    newline_duration = (newline_time - newline_start) * 1000
                    self.logger.debug(f'NEWLINE operation: {newline_duration:.2f}ms')
            elif key == Key.HOME:
                home_start = time.perf_counter()
                self.emulator.home()
                home_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    home_duration = (home_time - home_start) * 1000
                    self.logger.debug(f'HOME operation: {home_duration:.2f}ms')
            elif key == Key.UP:
                up_start = time.perf_counter()
                self.emulator.cursor_up()
                up_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    up_duration = (up_time - up_start) * 1000
                    self.logger.debug(f'UP operation: {up_duration:.2f}ms')
            elif key == Key.DOWN:
                down_start = time.perf_counter()
                self.emulator.cursor_down()
                down_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    down_duration = (down_time - down_start) * 1000
                    self.logger.debug(f'DOWN operation: {down_duration:.2f}ms')
            elif key == Key.LEFT:
                left_start = time.perf_counter()
                self.emulator.cursor_left()
                left_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    left_duration = (left_time - left_start) * 1000
                    self.logger.debug(f'LEFT operation: {left_duration:.2f}ms')
            elif key == Key.LEFT_2:
                left2_start = time.perf_counter()
                self.emulator.cursor_left(rate=2)
                left2_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    left2_duration = (left2_time - left2_start) * 1000
                    self.logger.debug(f'LEFT_2 operation: {left2_duration:.2f}ms')
            elif key == Key.RIGHT:
                right_start = time.perf_counter()
                self.emulator.cursor_right()
                right_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    right_duration = (right_time - right_start) * 1000
                    self.logger.debug(f'RIGHT operation: {right_duration:.2f}ms')
            elif key == Key.RIGHT_2:
                right2_start = time.perf_counter()
                self.emulator.cursor_right(rate=2)
                right2_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    right2_duration = (right2_time - right2_start) * 1000
                    self.logger.debug(f'RIGHT_2 operation: {right2_duration:.2f}ms')
            elif key == Key.BACKSPACE:
                backspace_start = time.perf_counter()
                self.emulator.backspace()
                backspace_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    backspace_duration = (backspace_time - backspace_start) * 1000
                    self.logger.debug(f'BACKSPACE operation: {backspace_duration:.2f}ms')
            elif key == Key.DELETE:
                delete_start = time.perf_counter()
                self.emulator.delete()
                delete_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    delete_duration = (delete_time - delete_start) * 1000
                    self.logger.debug(f'DELETE operation: {delete_duration:.2f}ms')
            elif key == Key.ERASE_EOF:
                erase_eof_start = time.perf_counter()
                self.emulator.erase_end_of_field()
                erase_eof_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    erase_eof_duration = (erase_eof_time - erase_eof_start) * 1000
                    self.logger.debug(f'ERASE_EOF operation: {erase_eof_duration:.2f}ms')
            elif key == Key.ERASE_INPUT:
                erase_input_start = time.perf_counter()
                self.emulator.erase_input()
                erase_input_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    erase_input_duration = (erase_input_time - erase_input_start) * 1000
                    self.logger.debug(f'ERASE_INPUT operation: {erase_input_duration:.2f}ms')
            elif key == Key.INSERT:
                insert_start = time.perf_counter()
                self._handle_insert_key()
                insert_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    insert_duration = (insert_time - insert_start) * 1000
                    self.logger.debug(f'INSERT operation: {insert_duration:.2f}ms')
            elif key == Key.DUP:
                dup_start = time.perf_counter()
                self.emulator.dup()
                dup_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    dup_duration = (dup_time - dup_start) * 1000
                    self.logger.debug(f'DUP operation: {dup_duration:.2f}ms')
            elif key == Key.FIELD_MARK:
                field_mark_start = time.perf_counter()
                self.emulator.field_mark()
                field_mark_time = time.perf_counter()
                if self.logger.isEnabledFor(logging.DEBUG):
                    field_mark_duration = (field_mark_time - field_mark_start) * 1000
                    self.logger.debug(f'FIELD_MARK operation: {field_mark_duration:.2f}ms')
            else:
                character_lookup_start = time.perf_counter()
                character = get_character_for_key(key)
                character_lookup_time = time.perf_counter()

                if character:
                    encoding_start = time.perf_counter()
                    byte = character.encode(self.character_encoding)[0]
                    encoding_time = time.perf_counter()

                    input_start = time.perf_counter()
                    self.emulator.input(byte, self.keyboard_insert)
                    input_time = time.perf_counter()

                    if self.logger.isEnabledFor(logging.DEBUG):
                        character_lookup_duration = (character_lookup_time - character_lookup_start) * 1000
                        encoding_duration = (encoding_time - encoding_start) * 1000
                        input_duration = (input_time - input_start) * 1000
                        self.logger.debug(f'Character input: lookup={character_lookup_duration:.2f}ms, encoding={encoding_duration:.2f}ms, input={input_duration:.2f}ms')
        except OperatorError as error:
            self.operator_error = error

        if self.logger.isEnabledFor(logging.DEBUG):
            total_handle_key_time = (time.perf_counter() - handle_key_start) * 1000
            aid_lookup_duration = (aid_lookup_time - aid_lookup_start) * 1000
            self.logger.debug(f'Handle key total: {total_handle_key_time:.2f}ms, aid_lookup={aid_lookup_duration:.2f}ms')

    def render(self):
        render_start = time.perf_counter()

        apply_start = time.perf_counter()
        self._apply()
        apply_time = time.perf_counter()

        # Note: Flush is now called before polling in the main loop to reduce latency
        # by batching display updates instead of flushing immediately after each keystroke

        total_render_time = (apply_time - render_start) * 1000
        apply_duration = (apply_time - apply_start) * 1000

        self.logger.debug(f'Render timing: total={total_render_time:.2f}ms, apply={apply_duration:.2f}ms (flush deferred)')

    def _reset_insert(self):
        if not self.keyboard_insert:
            return

        self.keyboard_insert = False

        self.terminal.display.status_line.write_keyboard_insert(False)

    def _handle_insert_key(self):
        self.keyboard_insert = not self.keyboard_insert

        self.terminal.display.status_line.write_keyboard_insert(self.keyboard_insert)

    def _connect_host(self):
        # We will pretend a 3279 without EAB is a 3278.
        if self.terminal.display.has_eab:
            type = '3279'
        else:
            type = '3278'

        # Although a IBM 3278 does not support the formatting enabled by the extended
        # data stream, the capabilities will be reported in the query reply.
        terminal_type = f'IBM-{type}-{self.terminal.terminal_id.model}-E'

        self.logger.info(f'Terminal Type = {terminal_type}')

        tn3270e_args = _get_tn3270e_args(self.tn3270e_profile)

        self.telnet = Telnet(terminal_type, **tn3270e_args)

        self.telnet.open(self.host, self.port, self.device_names)

        if self.telnet.is_tn3270e_negotiated:
            self.logger.info(f'TN3270E mode negotiated: Device Type = {self.telnet.device_type}, Device Name = {self.telnet.device_name}, Functions = {self.telnet.tn3270e_functions}')
        else:
            self.logger.debug('Unable to negotiate TN3270E mode')

    def _disconnect_host(self):
        self.telnet.close()

        self.telnet = None

    def _apply(self):
        apply_start = time.perf_counter()

        has_eab = self.terminal.display.has_eab

        cell_processing_start = time.perf_counter()
        cells_processed = 0
        for address in self.emulator.dirty:
            cell = self.emulator.cells[address]

            (regen_byte, eab_byte) = _map_cell(cell, self.character_encoding, has_eab)

            self.terminal.display.buffered_write_byte(regen_byte, eab_byte, index=address)
            cells_processed += 1
        cell_processing_time = time.perf_counter()

        dirty_clear_start = time.perf_counter()
        self.emulator.dirty.clear()
        dirty_clear_time = time.perf_counter()

        # Update the message area.
        message_area_start = time.perf_counter()
        self.message_area = self._format_message_area()
        message_area_time = time.perf_counter()

        if self.logger.isEnabledFor(logging.DEBUG):
            total_apply_time = (message_area_time - apply_start) * 1000
            cell_processing_duration = (cell_processing_time - cell_processing_start) * 1000
            dirty_clear_duration = (dirty_clear_time - dirty_clear_start) * 1000
            message_area_duration = (message_area_time - message_area_start) * 1000
            self.logger.debug(f'Apply: total={total_apply_time:.2f}ms, cell_processing={cell_processing_duration:.2f}ms, cells={cells_processed}, dirty_clear={dirty_clear_duration:.2f}ms, message_area={message_area_duration:.2f}ms')

    def _flush(self):
        flush_start = time.perf_counter()

        display_flush_start = time.perf_counter()
        self.terminal.display.flush()
        display_flush_time = time.perf_counter()

        # TODO: hmm we need a buffered status line...
        status_line_start = time.perf_counter()
        if self.message_area != self.last_message_area:
            self.terminal.display.status_line.write(8, self.message_area)
            self.last_message_area = self.message_area
        status_line_time = time.perf_counter()

        cursor_move_start = time.perf_counter()
        self.terminal.display.move_cursor(index=self.emulator.cursor_address)
        cursor_move_time = time.perf_counter()

        # TODO: This needs to be moved.
        self.operator_error = None

        if self.logger.isEnabledFor(logging.DEBUG):
            total_flush_time = (cursor_move_time - flush_start) * 1000
            display_flush_duration = (display_flush_time - display_flush_start) * 1000
            status_line_duration = (status_line_time - status_line_start) * 1000
            cursor_move_duration = (cursor_move_time - cursor_move_start) * 1000
            self.logger.debug(f'Flush: total={total_flush_time:.2f}ms, display_flush={display_flush_duration:.2f}ms, status_line={status_line_duration:.2f}ms, cursor_move={cursor_move_duration:.2f}ms')

    def _format_message_area(self):
        message_area = b''

        if self.waiting_on_host:
            # X SPACE CLOCK_LEFT CLOCK_RIGHT
            message_area = b'\xf6\x00\xf4\xf5'
        elif isinstance(self.operator_error, ProtectedCellOperatorError):
            # X SPACE ARROW_LEFT OPERATOR ARROW_RIGHT
            message_area = b'\xf6\x00\xf8\xdb\xd8'
        elif isinstance(self.operator_error, FieldOverflowOperatorError):
            # X SPACE OPERATOR >
            message_area = b'\xf6\x00\xdb' + encode_string('>')
        elif self.emulator.keyboard_locked:
            # X SPACE SYSTEM
            message_area = b'\xf6\x00' + encode_string('SYSTEM')

        return message_area.ljust(9, b'\x00')

def _map_cell(cell, character_encoding, has_eab):
    regen_byte = 0x00

    if isinstance(cell, AttributeCell):
        # Only map the protected and display bits - ignore numeric, skip and modified.
        regen_byte = 0xc0 | (cell.attribute.value & 0x2c)
    elif isinstance(cell, CharacterCell):
        byte = cell.byte

        if cell.character_set is not None:
            # TODO: Temporary workaround until character set support is added.
            regen_byte = encode_character('ß')
        elif byte == DUP:
            regen_byte = encode_character('*')
        elif byte == FM:
            regen_byte = encode_character(';')
        else:
            character = bytes([byte]).decode(character_encoding)

            regen_byte = encode_character(character)

    if not has_eab:
        return (regen_byte, None)

    eab_byte = _map_formatting(cell.formatting)

    return (regen_byte, eab_byte)

def _map_formatting(formatting):
    if formatting is None:
        return 0x00

    byte = 0x00

    # Map the 3270 color to EAB color.
    if formatting.color == Color.BLUE:
        byte |= 0x08
    elif formatting.color == Color.RED:
        byte |= 0x10
    elif formatting.color == Color.PINK:
        byte |= 0x18
    elif formatting.color == Color.GREEN:
        byte |= 0x20
    elif formatting.color == Color.TURQUOISE:
        byte |= 0x28
    elif formatting.color == Color.YELLOW:
        byte |= 0x30
    elif formatting.color == Color.WHITE:
        byte |= 0x38

    # Map the 3270 highlight to EAB highlight.
    if formatting.blink:
        byte |= 0x40
    elif formatting.reverse:
        byte |= 0x80
    elif formatting.underscore:
        byte |= 0xc0

    return byte

def _get_tn3270e_args(profile):
    is_tn3270e_enabled = True
    tn3270e_functions = [TN3270EFunction.RESPONSES]

    if profile == 'off':
        is_tn3270e_enabled = False
        tn3270e_functions = None
    elif profile == 'basic':
        tn3270e_functions = []

    return {
        'is_tn3270e_enabled': is_tn3270e_enabled,
        'tn3270e_functions': tn3270e_functions
    }
