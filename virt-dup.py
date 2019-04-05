from lib.libvirt_utils import LibvirtUtils, VirtDupXML, SnapshotManager
from lib.scheduler import Scheduler
from lib.config import Config
from pprint import pprint
import libvirt
from lib.qemu_utils import img_info
import logging

#config_path = "/etc/virt-dup.yml"

config_path = "/home/spencer/git-repos/virt-dup/virt-dup.yml"

config = Config(config_path)

Scheduler(config)
"""
lv = LibvirtUtils(config)

domain = lv.domain_search('ubuntu18.04')

xml = VirtDupXML(config, domain)
#xml.delete_all_jobs()
#xml.add_job()

for job in xml.loaded_jobs:
#    pass

    sm = SnapshotManager(config, xml, job)
    sm.stage_image(staging=False)
#    sm.block_commit()

#    with open("/home/spencer/virt-dup/test-snap.xml", 'w+') as file:
#        file.write(sm.gen_snapshot_xml())
"""

