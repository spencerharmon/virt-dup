import logging
logging.basicConfig(format='%(asctime)s %(levelname)s %(module)s %(threadName)s %(funcName)s "%(message)s"')
import sys


class LibvirtException(BaseException):
    def __init__(self, err):
        self.description = f"Received error message from libvirt: {err}"
        logging.warning(self.description)


class OpenFailed(LibvirtException):
    code = 501

    def __init__(self, uri):
        self.description = "Could not connect to uri: {}".format(uri)
        logging.warning(self.description)


class VirtDupXMLException(LibvirtException):
    code = 500


class JobNotFound(VirtDupXMLException):
    code = 500

    def __init__(self, uuid, domainname):
        self.description = f'UUID {uuid} not found on domain {domainname}.'
        logging.warning(self.description)


class SnapshotManagerException(LibvirtException):
    code = 500

    def __init__(self):
        self.description = "Problem with taking snapshots."
        logging.warning(self.description)


class NoSnapshot(SnapshotManagerException):
    def __init__(self, uuid):
        self.description = f'No snapshots found for job {uuid}'
        logging.warning(self.description)

class SnapshotExists(SnapshotManagerException):
    def __init__(self, uuid):
        self.description = f"Unable to complete job. Snapshot already exists for job {uuid}."
        logging.warning(self.description)

class DiskPivotException(SnapshotManagerException):
    def __init__(self, jobuuid, disk, message):
        self.description = f'Job:{jobuuid}, Disk {disk}, {message}'
        logging.warning(self.description)
