import yaml
import os


class Config(object):
    def __init__(self, path):
        self.path = path
        self.libvirt_user = ""
        self.libvirt_pw = ""
        self.libvirt_uri = "qemu+tcp://localhost/system"
        self.libvirt_flags = 0 #cannot be None
        self.libvirt_connection_type_socket = False

        self.parse(path)

    def parse(self, path):
        with open(path) as file:
            config = yaml.load(file)
        try:
            self.libvirt_user = config['libvirt-username']
        except:
            pass
        try:
            self.libvirt_pw = config['libvirt-password']
        except:
            pass
        try:
            self.libvirt_uri = config['libvirt-uri']
        except:
            pass
        try:
            self.libvirt_flags = config['libvirt-flags']
        except:
            pass
        try:
            self.libvirt_connection_type_socket = config['libvirt-socket']
        except:
            pass
        try:
            self.staging_path = config['staging-path']
        except:
            self.staging_path = '/var/lib/virt-dup/'
        try:
            self.depth = config['depth']
        except:
            self.depth = 0
        try:
            self.default_schedule = config['default-schedule']
        except:
            self.default_schedule = "0 0 * * *"


        print(config)


