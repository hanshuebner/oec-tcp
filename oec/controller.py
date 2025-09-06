"""
oec.controller
~~~~~~~~~~~~~~
"""

from enum import Enum
import time
import logging
import selectors
from concurrent import futures
from itertools import groupby
from coax import Poll, PollAck, KeystrokePollResponse, \
                 ReceiveTimeout, ReceiveError, ProtocolError

from .device import format_device, UnsupportedDeviceError
from .keyboard import Key
from .session import SessionDisconnectedError

class SessionState(Enum):
    """Session state."""

    STARTING = 1
    ACTIVE = 2
    TERMINATING = 3

class Controller:
    """The controller."""

    def __init__(self, interface, create_device, create_session):
        self.logger = logging.getLogger(__name__)

        self.interface = interface
        self.running = False

        self.create_device = create_device
        self.create_session = create_session

        self.device = None
        self.session = None
        self.session_state = None
        self.session_selector = None
        self.session_executor = None

        # Target time between POLL commands in seconds when a device is attached or
        # no device is attached.
        self.attached_poll_period = 1 / 15
        self.detached_poll_period = 1 / 2

        # Maximum number of POLL commands to execute, per attached device, per run
        # loop iteration. If all attached devices respond with TT/AR the run loop
        # iteration will exit without reaching this maximum depth.
        #
        # This is an effort to improve the keystroke responsiveness.
        self.poll_depth = 3

        self.last_attached_poll_time = None
        self.last_detached_poll_time = None

        # Track timing between keystrokes
        self.last_keystroke_time = None
        self.keystroke_count = 0

    def run(self):
        """Run the controller."""
        self.running = True

        self.session_selector = selectors.DefaultSelector()
        self.session_executor = futures.ThreadPoolExecutor()

        self.logger.info('Controller started')

        while self.running:
            self._run_loop()

        self.session_executor.shutdown(wait=True)

        self.session_executor = None

        if self.session_state == SessionState.ACTIVE:
            self._terminate_session(blocking=True)

        self.session_selector.close()

        self.session_selector = None

        self.session = None
        self.session_state = None
        self.device = None

        self.logger.info('Controller stopped')

    def stop(self):
        """Stop the controller."""
        self.running = False

    def _run_loop(self):
        loop_start = time.perf_counter()

        poll_delay = self._calculate_poll_delay()

        # If POLLing is delayed, handle the host output, otherwise just sleep.
        start_time = time.perf_counter()

        if poll_delay > 0:
            self._update_sessions(poll_delay)

        poll_delay -= (time.perf_counter() - start_time)

        if poll_delay > 0:
            time.sleep(poll_delay)

        # POLL device.
        poll_start = time.perf_counter()
        self._poll_device()
        poll_time = time.perf_counter()

        detached_poll_start = time.perf_counter()
        self._poll_for_device()
        detached_poll_time = time.perf_counter()

        total_loop_time = (time.perf_counter() - loop_start) * 1000
        poll_duration = (poll_time - poll_start) * 1000
        detached_poll_duration = (detached_poll_time - detached_poll_start) * 1000

        if self.logger.isEnabledFor(logging.DEBUG):
            # Enhanced run loop timing with breakdown
            session_update_duration = (poll_start - loop_start) * 1000
            self.logger.debug(f'Run loop: total={total_loop_time:.2f}ms, session_update={session_update_duration:.2f}ms, attached_poll={poll_duration:.2f}ms, detached_poll={detached_poll_duration:.2f}ms')

            # Log performance summary every 100 iterations for pattern analysis
            if hasattr(self, '_loop_count'):
                self._loop_count += 1
            else:
                self._loop_count = 1

            if self._loop_count % 100 == 0:
                self.logger.info(f'Performance summary after {self._loop_count} loops: avg_loop_time={total_loop_time:.2f}ms, avg_poll={poll_duration:.2f}ms, avg_detached_poll={detached_poll_duration:.2f}ms')

    def _update_sessions(self, duration):
        update_start = time.perf_counter()

        # Start session if device is attached but no session exists
        session_start_check = time.perf_counter()
        if self.device and not self.session:
            self._start_session()
        session_start_time = time.perf_counter()

        # Handle session state transitions
        state_transition_start = time.perf_counter()
        if self.session_state == SessionState.STARTING and isinstance(self.session, futures.Future):
            if self.session.done():
                session = self.session.result()
                self.session = session
                self.session_state = SessionState.ACTIVE
                self.session_selector.register(session, selectors.EVENT_READ)
                self.logger.info(f'Session started for device @ {format_device(self.interface)}')

        elif self.session_state == SessionState.TERMINATING and isinstance(self.session, futures.Future):
            if self.session.done():
                self.session = None
                self.session_state = None
                self.logger.info(f'Session terminated for device @ {format_device(self.interface)}')
        state_transition_time = time.perf_counter()

        # Update active session with enhanced timing
        updated_session = False
        host_processing_start = time.perf_counter()

        if self.session_state == SessionState.ACTIVE and duration > 0:
            start_time = time.perf_counter()

            # Enhanced session selection timing
            select_start = time.perf_counter()
            sessions = set(self._select_sessions(duration))
            select_time = time.perf_counter()
            select_duration = (select_time - select_start) * 1000

            if self.session in sessions:
                try:
                    # Enhanced host handling timing
                    handle_host_start = time.perf_counter()
                    if self.session.handle_host():
                        updated_session = True
                    handle_host_time = time.perf_counter()
                    handle_host_duration = (handle_host_time - handle_host_start) * 1000

                    self.logger.debug(f'Session handle_host: {handle_host_duration:.2f}ms (select: {select_duration:.2f}ms, handle: {handle_host_duration:.2f}ms)')
                except SessionDisconnectedError:
                    self._handle_session_disconnected()

            duration -= (time.perf_counter() - start_time)

        host_processing_time = time.perf_counter()

        render_start = time.perf_counter()
        if updated_session:
            self.session.render()
        render_time = time.perf_counter()

        total_update_time = (time.perf_counter() - update_start) * 1000
        host_duration = (host_processing_time - host_processing_start) * 1000
        render_duration = (render_time - render_start) * 1000
        session_start_duration = (session_start_time - session_start_check) * 1000
        state_transition_duration = (state_transition_time - state_transition_start) * 1000

        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(f'Session update: total={total_update_time:.2f}ms, session_start={session_start_duration:.2f}ms, state_transition={state_transition_duration:.2f}ms, host_processing={host_duration:.2f}ms, render={render_duration:.2f}ms, updated={updated_session}')

    def _select_sessions(self, duration):
        # The Windows selector will raise an error if there are no handles registered while
        # other selectors may block for the provided duration.
        if not self.session_selector.get_map():
            return []

        selected = self.session_selector.select(duration)

        return [key.fileobj for (key, _) in selected]

    def _start_session(self):
        self.logger.info(f'Starting session for device @ {format_device(self.interface)}')

        def start_session():
            session = self.create_session(self.device)

            session.start()

            return session

        future = self.session_executor.submit(start_session)

        self.session = future
        self.session_state = SessionState.STARTING

    def _terminate_session(self, blocking=False):
        self.logger.info(f'Terminating session for device @ {format_device(self.interface)}')

        if self.session_state == SessionState.ACTIVE:
            self.session_selector.unregister(self.session)

        def terminate_session():
            self.session.terminate()

        if blocking:
            terminate_session()
            self.session = None
            self.session_state = None
        else:
            future = self.session_executor.submit(terminate_session)
            self.session = future
            self.session_state = SessionState.TERMINATING

    def _handle_session_disconnected(self):
        self.logger.info('Session disconnected')

        self._terminate_session()

    def _poll_device(self):
        poll_start = time.perf_counter()
        self.last_attached_poll_time = time.perf_counter()

        if not self.device:
            return

        total_poll_execute_time = 0
        total_ack_time = 0
        total_response_handle_time = 0
        iterations_with_response = 0

        for poll_iteration in range(self.poll_depth):
            iteration_start = time.perf_counter()

            # Enhanced poll command creation timing
            poll_command_start = time.perf_counter()
            poll_action_start = time.perf_counter()
            poll_action = self.device.get_poll_action()
            poll_action_time = time.perf_counter()
            poll_command = Poll(poll_action)
            poll_command_time = time.perf_counter()

            # Enhanced poll execution timing
            poll_execute_start = time.perf_counter()
            poll_response = self.interface.execute(poll_command, receive_timeout_is_error=False)
            poll_execute_time = time.perf_counter()

            # Handle POLL response.
            if poll_response is not None and not isinstance(poll_response, ReceiveTimeout):
                # Enhanced ACK timing
                ack_start = time.perf_counter()
                self.interface.execute(PollAck())
                ack_time = time.perf_counter()

                # Enhanced response handling timing
                response_handle_start = time.perf_counter()
                self._handle_poll_response(poll_response)
                response_handle_time = time.perf_counter()

                iteration_time = (response_handle_time - iteration_start) * 1000
                poll_action_duration = (poll_action_time - poll_action_start) * 1000
                poll_command_duration = (poll_command_time - poll_command_start) * 1000
                poll_duration = (poll_execute_time - poll_execute_start) * 1000
                ack_duration = (ack_time - ack_start) * 1000
                response_handle_duration = (response_handle_time - response_handle_start) * 1000

                total_poll_execute_time += poll_duration
                total_ack_time += ack_duration
                total_response_handle_time += response_handle_duration
                iterations_with_response += 1

                self.logger.debug(f'Poll iteration {poll_iteration + 1}: total={iteration_time:.2f}ms, action={poll_action_duration:.2f}ms, command={poll_command_duration:.2f}ms, poll={poll_duration:.2f}ms, ack={ack_duration:.2f}ms, handle={response_handle_duration:.2f}ms, response=True')
            else:
                # Handle lost device.
                if isinstance(poll_response, ReceiveTimeout):
                    self._handle_device_lost()

                if poll_response is None or isinstance(poll_response, ReceiveTimeout):
                    break

        total_poll_time = (time.perf_counter() - poll_start) * 1000
        self.logger.debug(f'Total poll cycle: {total_poll_time:.2f}ms, iterations={iterations_with_response}, poll_execute={total_poll_execute_time:.2f}ms, ack={total_ack_time:.2f}ms, handle={total_response_handle_time:.2f}ms')

    def _poll_for_device(self):
        if self.last_detached_poll_time is not None and (time.perf_counter() - self.last_detached_poll_time) < self.detached_poll_period:
            return

        self.last_detached_poll_time = time.perf_counter()

        if self.device:
            return  # Device already attached

        try:
            poll_response = self.interface.execute(Poll())
        except ReceiveTimeout:
            return
        except ReceiveError as error:
            self.logger.warning(f'POLL for device @ {format_device(self.interface)} receive error: {error}')
            return
        except ProtocolError as error:
            self.logger.warning(f'POLL for device @ {format_device(self.interface)} protocol error: {error}')
            return

        if poll_response:
            try:
                self.interface.execute(PollAck())
            except ReceiveTimeout:
                self.logger.warning(f'POLL for device @ {format_device(self.interface)} PollAck timeout')
                return

        self._handle_device_found(poll_response)

    def _handle_device_found(self, poll_response):
        self.logger.info(f'Found device @ {format_device(self.interface)}')

        try:
            device = self.create_device(self.interface, poll_response)
        except UnsupportedDeviceError as error:
            self.logger.error(f'Unsupported device @ {format_device(self.interface)}: {error}')
            return

        device.setup()

        self.device = device

        self.logger.info(f'Attached device @ {format_device(self.interface)}')

    def _handle_device_lost(self):
        self.logger.info(f'Lost device @ {format_device(self.interface)}')

        if self.session_state == SessionState.ACTIVE:
            self._terminate_session()

        self.device = None

        self.logger.info(f'Detached device @ {format_device(self.interface)}')

    def _handle_poll_response(self, poll_response):
        if isinstance(poll_response, KeystrokePollResponse):
            self._handle_keystroke_poll_response(poll_response)

    def _handle_keystroke_poll_response(self, poll_response):
        keystroke_start_time = time.perf_counter()
        scan_code = poll_response.scan_code

        # Track timing between keystrokes
        current_time = keystroke_start_time
        if self.last_keystroke_time is not None:
            time_since_last_keystroke = (current_time - self.last_keystroke_time) * 1000
            self.logger.info(f'Time since last keystroke: {time_since_last_keystroke:.2f}ms')
        else:
            time_since_last_keystroke = 0
            self.logger.info('First keystroke detected')

        self.last_keystroke_time = current_time
        self.keystroke_count += 1

        self.logger.debug(f'Keystroke #{self.keystroke_count} detected at {keystroke_start_time:.6f}: Scan Code = {scan_code}')

        # Enhanced keyboard processing timing
        keyboard_lookup_start = time.perf_counter()
        (key, modifiers, modifiers_changed) = self.device.keyboard.get_key(scan_code)
        keyboard_processed_time = time.perf_counter()

        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug((f'Keystroke detected: Scan Code = {scan_code}, '
                               f'Key = {key}, Modifiers = {modifiers}'))
            keyboard_lookup_duration = (keyboard_processed_time - keyboard_lookup_start) * 1000
            self.logger.debug(f'Keyboard processing took {keyboard_lookup_duration:.2f}ms (lookup: {keyboard_lookup_duration:.2f}ms)')

        # Update the status line if modifiers have changed.
        if modifiers_changed:
            self.device.display.status_line.write_keyboard_modifiers(modifiers)

        if not key:
            return

        if key == Key.CURSOR_BLINK:
            self.device.display.toggle_cursor_blink()
        elif key == Key.ALT_CURSOR:
            self.device.display.toggle_cursor_reverse()
        elif key == Key.CLICKER:
            self.device.keyboard.toggle_clicker()
        elif self.session_state == SessionState.ACTIVE:
            # Enhanced session processing timing
            session_handle_start = time.perf_counter()
            self.session.handle_key(key, modifiers, scan_code)
            session_handle_time = time.perf_counter()

            session_latency = (session_handle_time - session_handle_start) * 1000
            self.logger.debug(f'Session key handling took {session_latency:.2f}ms')

            # Enhanced render timing with detailed breakdown
            render_start = time.perf_counter()
            self.session.render()
            render_time = time.perf_counter()

            total_latency = (render_time - keystroke_start_time) * 1000
            keyboard_latency = (keyboard_processed_time - keystroke_start_time) * 1000
            render_latency = (render_time - render_start) * 1000

            # Calculate processing overhead
            processing_overhead = total_latency - keyboard_latency - session_latency - render_latency

            self.logger.info(f'Keystroke #{self.keystroke_count} total latency: {total_latency:.2f}ms (keyboard: {keyboard_latency:.2f}ms, session: {session_latency:.2f}ms, render: {render_latency:.2f}ms, overhead: {processing_overhead:.2f}ms)')

            # Calculate the "missing time" - time that can't be accounted for
            if time_since_last_keystroke > 0:
                accounted_time = total_latency
                missing_time = time_since_last_keystroke - accounted_time
                if missing_time > 0:
                    self.logger.warning(f'Missing time between keystrokes: {missing_time:.2f}ms (gap: {time_since_last_keystroke:.2f}ms, accounted: {accounted_time:.2f}ms)')

    def _calculate_poll_delay(self):
        if self.last_attached_poll_time is None:
            return 0

        return max((self.last_attached_poll_time + self.attached_poll_period) - time.perf_counter(), 0)

