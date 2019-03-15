# virt-dup 
Virt-dup is intended to be a lightweight backup manager for qcow2 disks
in KVM virtual machines integrated with libvirt.

## Libvirt Domain XML Metadata
In addition to providing backend information and a private key for
duplicity and connection information for libvirt, the only necessary
step to begin backing up virtual machines is to add a job to the libvirt
domain. Doing so will add a metadata element to the domain xml called
virt-dup:instance and a subelement called virt-dup:job. The minimum xml
to enable backups for a domain's qcow2 disks is the following:
```
<virt-dup:instance xmlns:virt-dup="https://www.github.com/spencerharmon/virt-dup">
    <virt-dup:job />
</virt-dup:instance>
```
The above configuration can be achieved by enerting the command
`virt-dup add-job [domain]` where \[domain] is the name or uuid of the
domain to be enabled. You may optionally specify parameters like
`--path` if you'd like to specify a certain disk to backup by its path
or `--schedule` to provide a cron string for a custom schedule for this
domain. For a complete list of options, see the `virt-dup help` text.
## virt-dup.yml
This config file defines the information needed for virt-dup to connect
to libvirt and any duplicity backends. It also sets defaults for backup
job parameters like schedule, retention, backends, and encryption key.
## Config Options
Config options can be set globally in virt-dup.yml or in job xml stored
in libvirt domain metadata. 
### schedule
The value of a schedule is a cron string representing the schedule at
which backups should be run. The default schedule can be set in the
virt-dup.yml configuration file using the "default_schedule" option.
There is plenty of documentation around the web about how cron strings
are structured, so I won't rehash that here. Just know that virt-dup
parses cron strings exactly like cron. Backups are queued just-in-time,
and config changes either to the domain xml or to virt-dup.yml are
picked up automatically. There is no need to restart virt-dup after
making these, or other, configuration changes. 
### depth
Depth determines how many backing files to copy if a backup job is run
on a virtual disk with external snapshots. 

A depth of 1 backs up only the topmost file (the snapshot created at the
time the backup is run). Depth 2 backs up the the topmost file and its
backing store, et c..

The default depth is 0. This backs up all files in the chain.

Negative integers back up all files in the chain, minus the value given,
excluding backing stores starting with the base of the chain. That is,
specifying a depth of -1 excludes the bottommost backing store for the
given disk. Be careful, this will happen even if there is a single image
with no backing chain; in this case, the only image would be excluded
from the backing chain.

The intention of providing a depth option is to support cases in which
multiple VMs are referencing only one backing store. This way, one job
can be assigned to backup its VM's domain-specific image and the shared
backing store (depth 0), while the jobs for the other VMs can specify
depth -1 to exclude the backing store.

The term depth is often used when referring to tree structures as is the
case with disk image snapshots, which is why this term was selected.
Unfortunately, it only makes sense to measure depth of any kind from the
top, and in the case of robust backups, this is the opposite of the
desired effect. Consider that you explicitly set depth of 1 intending to
backup the data for a vm using a backing store otherwise backed up by a
different job. That is, the VM is configured:

`[base.qcow2] -> [vm01.qcow2]`

If you take a manual snapshot of this vm in livbirt, you may get:
 
 `[base.qcow2] -> [vm01.qcow2] -> [vm01.qcow2.snap]`
 
 
If this snapshot remains during the scheduled backup job, the depth
setting will include only the topmost image, vm01.qcow2.snap, which will
be invalid without its backing store.

This is the justification for the only useful settings being 0 and
negative integers. It logically followed that depth 0 should be the
whole chain, and "less than the whole chain by 1" makes enough sense.
The positive integers are kept around mostly as a byproduct of this
idea.

Although the depth option is available to set as global default in
virt-dup.yml, it is not recommended to change this setting because of
the likelihood of resulting in invalid backups for VMs which don't have
their depth explicitly set. Instead, you should set depth explicitly on
VMs in the shared backing store case, specifying the relevant drive in
the job, and excluding it on any other jobs for the libvirt domain,
assuming the intention is not to backup the backing store with more than
one job (if this isn't a concern, there is no need to change the depth).
## Status
### Implemented
- YML config file
- Domain specific configurations stored in and read from libvirt domain
  xml namespace
- Connection via localhost tcp connection with no auth
- libvirt snapshot management for live vms
- libvirt snapshot management for powered-off vms
- handles power-offs and power-ons during backup job
- leave-it-how-you-found-it approach to existing snapshots/shared
  backing images
- snapshot depth control for shared backing images
- cron-like scheduler
- automatic detection of config file changes when running in daemon mode

  

### Must Have
- commandline argument parsing
  - add-job \[domain] \[options]
  - remove-job __job_uuid__
  - remove-all-jobs
  - restore __domain__ \[snapshot-id]
  - help
- test other libvirt connection types
- duplicity interface
- retention policy management
- restore from backup
- daemon mode
- systemd unit files


### Nice to have
- proper python packaging
- management server for configuration and monitoring
- testing framework 
- alerting
- backend migration
- backup verification
- tab completion
  - https://argcomplete.readthedocs.io/en/latest/
- job options
  - disable-if-powered-off
