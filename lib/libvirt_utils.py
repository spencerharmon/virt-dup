import libvirt
from lib.exceptions.libvirt_exceptions import OpenFailed, \
    JobNotFound, NoSnapshot, DiskPivotException, SnapshotExists, LibvirtException
from lib import qemu_utils
import xml.etree.ElementTree as ET
import uuid
import os
import shutil
import time
from typing import List


class LibvirtUtils(object):
    def __init__(self, config):
        self.config = config
        self.user = config.libvirt_user
        self.pw = config.libvirt_pw
        auth = [[libvirt.VIR_CRED_AUTHNAME, libvirt.VIR_CRED_PASSPHRASE], self._auth_callback, None]
        if config.libvirt_connection_type_socket:
            self.conn = libvirt.open('qemu:///system')
        else:
            self.conn = libvirt.openAuth(config.libvirt_uri, auth, config.libvirt_flags)
        if self.conn is None:
            raise OpenFailed(config.libvirt_uri)

        self.domainnames = {}
        self.domainuuids = {}
        self.domain_disks = {}
        self.get_updated_domain_info()

    def _auth_callback(self, credentials, user_data):
        for credential in credentials:
            if credential[0] == libvirt.VIR_CRED_AUTHNAME:
                credential[4] = self.user
            elif credential[0] == libvirt.VIR_CRED_PASSPHRASE:
                credential[4] = self.pw
            else:
                return -1
        return 0

    def shutdown_callback(self):
        self.conn.close()

    def get_updated_domain_info(self):
        try:
            for domain in self.conn.listAllDomains(0):
                self.domainnames[domain.name()] = domain
                self.domain_disks[domain.name()] = VirtDupXML(self.config, domain).disk_summary()
                self.domainuuids[domain.UUIDString()] = domain
        except libvirt.libvirtError as e:
            raise LibvirtException(e.err)

    def domain_search(self, str):
        self.get_updated_domain_info()

        for uuid in self.domainuuids:
            if uuid == str:
                return self.domainuuids[str]

        for name in self.domainnames:
            if name == str:
                return self.domainnames[str]

    def snapshot(self, dom):
        pass


class SnapshotManager(object):
    '''
    correlates metadata, takes snapshots, and otherwise makes things ready for copying offsite via duplicity
    '''
    def __init__(self, config, domxml, jobuuid):
        self.config = config
        self.domxml = domxml
        self.job = self.domxml.loaded_jobs[jobuuid]
        self.job_staging_path = self.get_staging_path()

    def get_staging_path(self):
        path = f"{self.config.staging_path}{self.job['uuid']}/"
        # make sure it exists
        os.makedirs(path, exist_ok=True)
        return path

    def stage_image(self, staging=True):
        self.create_snapshot()
        files = self.get_file_list()
        print(files)
        if staging:
            #todo: fork copy to staging threads
            for source, dest in files.items():
                pathname = f"{self.config.staging_path}/{self.job['uuid']}/{dest}"
                shutil.copyfile(source, pathname)
                # todo: sometimes extra slashes. Bash doesn't care, but it doesn't look nice.
                print(pathname)
        while True:
            try:
                self.block_commit()
                break
            except DiskPivotException:
                # restart block commit if disk pivot fails.
                pass
            except Exception as e:
                raise e

    def gen_snapshot_xml(self):
        '''
        https://libvirt.org/formatsnapshot.html
        :return: string containing xml data for libvirt snapshot
        '''
        xml = ET.Element('domainsnapshot')
        description = ET.Element('description')
        #todo
        #this is how we identify snapshots that are ours. No chance of collisions, right? right?
        description.text = self.job['uuid']
        xml.append(description)
        memory = ET.Element('memory', {'snapshot': 'no'})
        xml.append(memory)
        if 'path' or 'dev_name' in self.job.keys():
            disks = ET.Element('disks')
            xml.append(disks)
            for disk in self.domxml.disk_summary():
                attributes = {}
                attributes['name'] = disk['path']
                if disk['backup-enabled']:
                    attributes['snapshot'] = 'external'
                else:
                    attributes['snapshot'] = 'no'
                disk_e = ET.Element('disk', attributes)
                if disk['backup-enabled']:
                    source = ET.Element('source', {'file': disk['path'] + '.virt-dup-snap'})
                    disk_e.append(source)
                disks.append(disk_e)

        return ET.tostring(xml).decode()

    def create_snapshot(self):
        '''
        Checks for existing snapshot for this job (raises error if snapshot exists).
        creates a full disk image using the atomic disk image snapshot feature. Writes are redirected
        to a mirror while the backing image is copied to a staging location or offiste. when complete, the mirror
        is block-copied back to the backing image, and duplicity is signaled that a file is ready for
        duplication offsite. Atomic has no effect for single-disk backups since we don't have to worry
        about the state of disks other than the one we're backing up.

        Problem: If the disk has existing internal or external snapshot(s), it needs to be merged after
        copy of the intended images.

        Questions: Quiesce? What happens if there's no QEMU agent?
            QEMU Agent detection?
            Shared backing disks? Configure behavior in job? Sane defaults?
            Existing snapshots?

        :return:
        '''
        # We shouldn't have a snapshot yet, so invert the logic of load_our_snapshot. Raise an error if
        # load_our_snapshot doesn't.
        try:
            self.load_our_snapshot()
        except NoSnapshot:
            pass
        else:
            raise SnapshotExists
        self.domxml.domain.snapshotCreateXML(
            self.gen_snapshot_xml(),
            libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_ATOMIC | libvirt.VIR_DOMAIN_SNAPSHOT_CREATE_DISK_ONLY)
        self.load_our_snapshot()

    def get_file_list(self):
        """
        Handles enumeration of files for backup after snapshots are made and destination file naming
        Parses disk image data to determine sequence. Establishes depth rules.
        File names are the dev name of the disk image, then epoch time (all in job run should match), then sequence
        number.

        :return: Dict. Paths to copy. format is: '/source/path': 'destination.filename'
        E.g:
        {'/var/lib/libvirt/images/vm01.cow2': 'vda-1551669947-0.cow2'}
        """
        disks = self.get_snap_files()
        timestamp = str(int(time.time()))
        ret = {}
        for disk, info in disks.items():
            img_info = qemu_utils.img_info(info['base'])
            cur = info['base']
            chain = {}
            # first build an accurate chain of any external snapshots
            # may need to loop through more than once in case the list items come back in an unexpected order.
            while True:
                found_base = False
                for img in img_info:

                    if img['filename'] == cur:
                        # format is base: top to we have easy access to the targeted image, and can count down for depth.
                        try:
                            chain[img['backing-filename']] = cur
                            cur = img['backing-filename']
                        except KeyError:
                            #no backing file means base image
                            chain['base'] = cur
                            found_base = True
                if found_base:
                    break
            # then generate filenames and filter out any backing files outside our depth.
            if self.job['depth'] <= 0:
                excl = abs(self.job['depth'])
            else:
                excl = len(img_info) - self.job['depth']
            for seq in range(len(img_info)):
                if seq == 0:
                    key = chain['base']
                else:
                    key = chain[key]
                if seq >= excl:
                    ret[key] = f"{disk}-{timestamp}-{seq}.qcow2"

        return ret
        # todo: info file in staging directory to keep image metadata with the backed-up images

    def load_our_snapshot(self):
        ret = None
        snapshots = self.domxml.domain.listAllSnapshots()
        for snap in snapshots:
            xml = ET.fromstring(snap.getXMLDesc(0))
            description = xml.find('description')
            try:
                if description.text == self.job['uuid']:
                    ret = snap
            except AttributeError:
                pass
        if ret is None:
            raise NoSnapshot(self.job['uuid'])
        else:
            return ret

    def orig_xml_from_snap(self, dev):
        snapshot = self.load_our_snapshot()
        xml = ET.fromstring(snapshot.getXMLDesc(0))
        domain = xml.find('domain')
        devices = domain.find('devices')
        disks = devices.findall('disk')
        for disk in disks:
            if disk.find('target').attrib['dev'] == dev:
                return disk

    def block_commit(self):
        '''
        block_commit cleans up after taking our snapshot and copying the backing disk to the staging area.
        plug in the info about the top/base, set the flags
        Active flag allows running VMs to pivot back to Base
        https://libvirt.org/html/libvirt-libvirt-domain.html#virDomainBlockCommitFlags
        Block copy sounds like a pretty good option, but it doesn't work in the case that there's an
        existing snapshot since the whole disk ends up consolodated in a new file. Maybe useful in a restore, though.
        :return:
        '''
        for disk, info in self.get_snap_files().items():
            #todo: thread for wait on job to finish. BIG BOTTLENECK
            if self.domxml.domain.state()[0] == libvirt.VIR_DOMAIN_RUNNING:
                self.domxml.domain.blockCommit(
                    disk,
                    info['base'],
                    info['top'],
                    0,
                    libvirt.VIR_DOMAIN_BLOCK_COMMIT_ACTIVE |
                    libvirt.VIR_DOMAIN_BLOCK_COMMIT_RELATIVE
                )
                self.pivot_disk(disk, info['base'])
            else:
                # libvirt as of 5.0.0 cannot block commit a volume on a domain that isn't running. Use QEMU instead
                #todo: starting a domain while performing a block-commit with qemu would be bad. How to prevent this?
                # This is especially true becauseof the updateDeviceFlags operation, since the
                qemu_utils.block_commit(info['top'], base=info['base'])

                # have to inform libvirt of the changes,
                self.pivot_disk(disk, info['base'], qemu_commit=True)

        #remove snapshot data/metadata
        os.remove(info['top'])
        self.load_our_snapshot().delete(libvirt.VIR_DOMAIN_SNAPSHOT_DELETE_METADATA_ONLY)

    def pivot_disk(self, dev, base, qemu_commit=False):
        '''
        ensure libvirt cleanly is updated of changes to underlying disk images and handle on- and offline pivoting
        handles edge cases like if the vm is powered off after the block commit and before libvirt xml is updated.
        :param dev: dev name from domain xml
        :param base:
        :param qemu_commit: true if qemu's  block commit was used prior to calling pivot_disk
        :return:
        '''
#        input("Paused. Press enter after change simulated")
        # first, check if the domain is running since the block commit was completed.
        if qemu_commit:
            if self.domxml.domain.state()[0] == libvirt.VIR_DOMAIN_RUNNING:
                # it's a bad idea to change the backing store to the base snapshot on a running vm
                # after running qemu-img commit.
                # in this state, we'll raise an error. the primary use case for this is block_commit,
                # where block_commit can be restarted, and libvirt's block job commands can be used instead.
                raise DiskPivotException(self.job['uuid'], dev, "Domain running after qemu block commit.")
            else:
                #safe to "manual pivot" (we hope)
                self.domxml.domain.updateDeviceFlags(
                    ET.tostring(self.orig_xml_from_snap(dev)).decode(),
                    libvirt.VIR_DOMAIN_AFFECT_CONFIG
                )
        elif self.domxml.domain.state()[0] == libvirt.VIR_DOMAIN_RUNNING:
            # make sure that block job is finished
            while True:
                time.sleep(1)
                try:
                    status = self.domxml.domain.blockJobInfo(dev, 0)
                    if status['cur'] == status['end']:
                        self.domxml.domain.blockJobAbort(dev, libvirt.VIR_DOMAIN_BLOCK_JOB_ABORT_PIVOT)
                        break
                except libvirt.libvirtError:
                    #libvirt error raised when domain shut down after previous test
                    raise DiskPivotException(self.job['uuid'], dev, "Libvirt blockjob error.")
        else:
            # not a qemu commit and domain not running. raise error.
            raise DiskPivotException(self.job['uuid'], dev, "Domain not running after libvirt block commit.")

    def get_snap_files(self):
        '''
        in order to make sure that we leave things the way we found them with respect to snapshots (and don't
        cavalierly block-commit over someone's shared backing image), get_snap_files looks through the snapshot xml
        for the correct paths to provide to block_commit.
        :return: multilevel dict with parent nodes representing the dev name of the disks snapshotted by this job
        and child nodes for the top and base disk image paths, as below:

        {
            'vda': {
                {'base': '/images/vm01-disk0.qcow2'
                'top': '/images/vm01-disk0.qcow2.virt-dup-snap'
            },
            'vdb': {
                'base': '/images/vm01-disk1.qcow2'
                'top': '/images/vm01-disk1.qcow2.virt-dup-snap'
            }
        }
        '''
        #todo: does list descendants flag cover enough use cases? Do we need to care if prior snapshots
        # include memory state? Do we care about external vs internal?
        # https://libvirt.org/html/libvirt-libvirt-domain-snapshot.html#VIR_DOMAIN_SNAPSHOT_LIST_ROOTS
        ret = {}
        xml = ET.fromstring(self.load_our_snapshot().getXMLDesc(0))
        #disks in top-level element contain top, disks in domain.devices contain base.
        for snapdisk in xml.find('disks').findall('disk'):
            if snapdisk.attrib['snapshot'] != 'no':
                top = snapdisk.find('source').attrib['file']
                domdisks = xml.find('domain').find('devices').findall('disk')
                base = None
                for disk in domdisks:
                    if disk.find('target').attrib['dev'] == snapdisk.attrib['name']:
                        base = disk.find('source').attrib['file']
                ret[snapdisk.attrib['name']] = {'base': base, 'top': top}
        return ret

    def incremental_backup(self):
        '''
        creates a differential image from the most recent backup chain for this job. creates a full
        snapshot as in create_snapshot(), then creates an empty image based on the snapshot, then rebases
        the new image on the most recent in the backup chain. This is a much more expensive process than
        full backups, but most backup software can do it, so there you go.
        https://kashyapc.fedorapeople.org/virt/img-diff-test.txt

        NOTE: duplicity has an incremental backup feature. Need to determine if this will work for VM
        incrementals given two fulls. Rebase may not be necessary.
        :return:
        '''
        self.domxml.domain.blockrebase()


class VirtDupXML(object):
    '''
    Generates XML for libvirt. Handles changes for virt-dup metadata.
    '''
    def __init__(self, config, domain):
        self.config = config
        self.domain = domain
        self.xmlroot = ET.fromstring(domain.XMLDesc())
        self.namespace = {'virt-dup': 'https://www.github.com/spencerharmon/virt-dup'}
        self.metadata = self.get_metadata_element()
        self.disk_list = [disk['dev_name'] for disk in self.disk_list()]
        self.loaded_jobs = self.load_jobs()

    def disk_list(self):
        """
        needs fixed. repeat of summary but avoids circular dependancy.
        :return:
        """
        disks = []
        for devices in self.xmlroot.findall('devices'):
            # only look within devices to avoid reporting disk elements
            # inside metadata elements
            for disk in devices.findall('disk'):
                driver = disk.find('driver')
                # todo: question: are there any sane backups to do on non-qcow disks?
                if 'type' in driver.attrib.keys() and driver.attrib['type'] == 'qcow2':
                    path = disk.find('source').attrib['file']
                    dev_name = disk.find('target').attrib['dev']
                    dev_info = {'path': path,
                                'dev_name': dev_name,
                                }
                    disks.append(dev_info)

        return disks

    def get_metadata_element(self):
        '''
        Creates metadata element if none exists; returns one if it's there.
        :return: ElementTree element for metadata element in domain's xml
        '''
        extant_element = self.xmlroot.find('metadata')
        if extant_element is None:
            metadata = ET.Element('metadata')
            self.xmlroot.append(metadata)
            return metadata
        else:
            return extant_element

    def get_virt_dup_element(self):
        """
        Returns an element no matter what. Gives the existing instance if it exists,
        makes a fresh one otherwise.
        :return: ElementTree element containing and instance of the virt-up namespace.
        As below:

        """
        # some ugly code to check the xml we already have for virt-dup instances
        # Although we're catching the libvirt errors, python bindings as they are
        # don't suppress the stderr from the C code executed by libvirt. This way,
        # we don't make calls unnecessarily.
        meta = self.xmlroot.find('metadata')
        try:
            instance = meta.find('virt-dup:instance', self.namespace)
        except AttributeError:
            instance = None
        if instance is not None:
            try:
                xml = self.domain.metadata(
                    libvirt.VIR_DOMAIN_METADATA_ELEMENT,
                    self.namespace['virt-dup'],
                    0
                )
                return ET.fromstring(xml)
            except libvirt.libvirtError as e:
                if 'Requested metadata element is not present' in e.err:
                    pass
                else:
                    raise e
        virt_dup = ET.Element('instance')
        return virt_dup

    def load_jobs(self):
        '''
        loads job details from libvirt xml, and sets defaults from config where applicable
        :return: dict containing job details
        '''
        virt_dup = self.get_virt_dup_element()
        jobs = {}
        for job in virt_dup.findall('job'):
            uuid = job.attrib['uuid']
            jobs[uuid] = job.attrib
            if 'depth' in jobs[uuid].keys():
                jobs[uuid]['depth'] = int(jobs[uuid]['depth'])
            else:
                jobs[uuid]['depth'] = self.config.depth
            if 'schedule' not in jobs[uuid].keys():
                jobs[uuid]['schedule'] = self.config.default_schedule
            if job.find('dev') is None:
                jobs[job.attrib['uuid']]['dev_names'] = self.disk_list
            else:
                jobs[job.attrib['uuid']]['dev_names'] = [dev.text for dev in job.findall('dev')]
        return jobs

    def add_job(self, dev_names: List = None, **kwargs):
        #Warning: all attribute values must be strings.
        job_attributes = {}
        try:
            #the extra business on the right-hand side of the declaration is to raise an error
            #if the user provides an invalid UUID and replace it with a valid uuid.
            #todo: give a warning when this happens.
            job_attributes['uuid'] = str(uuid.UUID(kwargs['uuid'], version=4))
        except:
            job_attributes['uuid'] = str(uuid.uuid4())
        try:
            job_attributes['path'] = kwargs['path']
        except:
            pass
        try:
            job_attributes['backends'] = kwargs['backends']
        except:
            pass
        try:
            job_attributes['description'] = kwargs['description']
        except:
            job_attributes['description'] = "Created by virt-dup. https://www.github.com/spencerharmon/virt-dup"
        try:
            job_attributes['full_schedule'] = kwargs['schedule']
        except:
            pass
        try:
            job_attributes['incremental_schedule'] = kwargs['incremental_schedule']
        except:
            pass
        try:
            job_attributes['full_retention'] = kwargs['full_retention']
        except:
            pass
        try:
            job_attributes['incremental_retention'] = kwargs['incremental_retention']
        except:
            pass
        try:
            job_attributes['depth'] = kwargs['depth']
        except:
            pass

        job_element = ET.Element('job', job_attributes)

        if dev_names is not None:
            for dev in dev_names:
                e = ET.Element('dev')
                e.text = dev
                job_element.append(e)
        virt_dup = self.get_virt_dup_element()
        virt_dup.append(job_element)
        self.replace_virt_dup_meta_with(virt_dup)
        self.load_jobs()

    def remove_job(self, uuid):
        found = False
        virt_dup = self.get_virt_dup_element()
        for job in self.loaded_jobs:
            if job.attrib['uuid'] == uuid:
                found = True
                virt_dup.remove(job)
        if found:
            self.replace_virt_dup_meta_with(virt_dup)
        else:
            raise JobNotFound(uuid, self.domain.name())
        self.load_jobs()

    def delete_all_jobs(self):
        self.replace_virt_dup_meta_with(None)
        self.load_jobs()

    def replace_virt_dup_meta_with(self, virt_dup):
        '''

        :param virt_dup: element tree 'virt-dup' element or None. deletes all jobs if None.
        :return: None if successful. Raises error otherwise.
        '''
        xml = None if virt_dup is None else ET.tostring(virt_dup).decode()
#        xml= '<instance><job dev_name="vda" /></instance>'
        # here is where libvirt magically adds all of the xmlns details
        self.domain.setMetadata(
            libvirt.VIR_DOMAIN_METADATA_ELEMENT,
            xml,
            'virt-dup',
            self.namespace['virt-dup'], 0)

    def disk_summary(self):
        '''

        :return: dict in the below structure. There are two top-level keys in the dict, path and dev_name.
        These keys contain lists which are redundant with one another. Entries in each list are dicts with
        keys corresponding to the top-level key (a path if it's in 'path' or a dev name if it's in 'dev_name'.
        The purpose of this is to be able to locate information about a disk using either the dev_name or the
        path as a key to avoid repeating cumbersome logic for iterating through a dict that contains only one
        or the other.
        [
            {
                'dev_name': vda
                'path': '/etc/libvirt/qemu/vda.qcow2',
                'backed-up': True,
                'target': ['rsync://backupserver//backups']
                'schedule': None
            },
            {
                'dev_name': vdb,
                'path': '/etc/libvirt/qemu/vdb.qcow2',
                'backed-up': True,
                'target': None
                'schedule': '* * * * * *'
            }
        ]

        '''
        disks = []
        for devices in self.xmlroot.findall('devices'):
            # only look within devices to avoid reporting disk elements
            # inside metadata elements
            for disk in devices.findall('disk'):
                driver = disk.find('driver')
                # todo: question: are there any sane backups to do on non-qcow disks?
                # answer: yes, but there may be caveats restoring raw disks since they will be
                # qcow2 format and therefore invalidate existing domain xml. We can fix that,
                # but not right now.
                if 'type' in driver.attrib.keys() and driver.attrib['type'] == 'qcow2':
                    path = disk.find('source').attrib['file']
                    dev_name = disk.find('target').attrib['dev']
                    dev_info = {'path': path,
                                'dev_name': dev_name,
                                'backup-enabled': False,
                                'jobs': []}
                    for jobuuid, jobinfo in self.loaded_jobs.items():
                        if 'path' in jobinfo.keys():
                            if jobinfo['path'] == path:
                                dev_info['backup-enabled'] = True
                                dev_info['jobs'].append(jobuuid)
                        elif 'dev_name' in jobinfo.keys():
                            if jobinfo['dev_name'] == dev_name:
                                dev_info['backup-enabled'] = True
                                dev_info['jobs'].append(jobuuid)
                        else:
                            #specifying no device or path enables backup on all disks.
                            dev_info['backup-enabled'] = True
                            dev_info['jobs'].append(jobuuid)

                    disks.append(dev_info)

        return disks

