import logging

logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(threadName)s %(funcName)s "%(message)s"')


class QemuException(Exception):
    def __init__(self):
        self.description = "Generic QEMU exception."
        logging.warning(self.description)

class BlockCommitException(QemuException):
    def __init__(self, stderr):
        self.description = f"`qemu-img commit ...` gave the following error: {stderr}"
        logging.warning(self.description)
