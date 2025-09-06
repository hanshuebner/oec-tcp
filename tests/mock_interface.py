from unittest.mock import Mock, ANY

from coax import ProtocolError, ReceiveError, ReceiveTimeout
from coax.interface import Interface

class MockInterface(Interface):
    def __init__(self, responses=[]):
        super().__init__()

        self.mock_responses = responses

        self.serial = Mock(port='/dev/mock')

        self.legacy_firmware_detected = None
        self.legacy_firmware_version = None

        # Wrap the reset and execute methods so calls can be asserted.
        self.reset = Mock(wraps=self.reset)
        self._execute = Mock(wraps=self._execute)

    def _execute(self, commands, timeout):
        if isinstance(commands, list):
            return [self._mock_get_response(command) for command in commands]
        else:
            return self._mock_get_response(commands)

    def reset_mock(self):
        self.reset.reset_mock()
        self._execute.reset_mock()

    def assert_command_executed(self, command_type, predicate=None):
        if not self._mock_get_execute_commands(command_type, predicate):
            raise AssertionError('Expected command to be executed')

    def assert_command_not_executed(self, command_type, predicate=None):
        if self._mock_get_execute_commands(command_type, predicate):
            raise AssertionError('Expected command not to be executed')

    def _mock_get_execute_commands(self, command_type, predicate):
        calls = self._execute.call_args_list

        commands = []

        for call in calls:
            commands_list = call[0][0]
            if isinstance(commands_list, list):
                for command in commands_list:
                    if isinstance(command, command_type):
                        if predicate is None or predicate(command):
                            commands.append(command)
            else:
                if isinstance(commands_list, command_type):
                    if predicate is None or predicate(commands_list):
                        commands.append(commands_list)

        return commands

    def _mock_get_response(self, command):
        for (mock_command_type, mock_predicate, mock_response) in self.mock_responses:
            if isinstance(command, mock_command_type):
                if mock_predicate is None or mock_predicate(command):
                    if callable(mock_response):
                        try:
                            return mock_response()
                        except (ProtocolError, ReceiveError, ReceiveTimeout) as error:
                            return error

                    return mock_response

        return None
