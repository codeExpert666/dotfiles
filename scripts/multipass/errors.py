"""命令执行器和宿主机编排共用的异常类型。"""


class Failure(Exception):
    def __init__(self, message, code=1):
        super().__init__(message)
        self.code = code


class CommandFailure(Failure):
    def __init__(self, argv, code, output, *, summary=None):
        self.argv = argv
        self.output = output
        diagnostic = output if summary is None else summary
        super().__init__(f"{' '.join(map(str, argv[:3]))} failed (exit {code}): {diagnostic[-1200:]}")
        self.returncode = code
