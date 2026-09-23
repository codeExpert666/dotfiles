"""Failure types shared by the command runner and host orchestration."""


class Failure(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class CommandFailure(Failure):
    def __init__(self, argv, code, output):
        self.argv = argv
        self.output = output
        super().__init__(f"{' '.join(map(str, argv[:3]))} failed (exit {code}): {output[-1200:]}")
        self.returncode = code
