import os
import time
from lib.config import Config
import multiprocessing
import queue
from lib.libvirt_utils import LibvirtUtils, VirtDupXML, SnapshotManager
from croniter import croniter
import signal
import sys
from lib.exceptions.libvirt_exceptions import LibvirtException


class Scheduler(object):
    def __init__(self, config):
        self.config = config
        self.config_mtime = os.path.getmtime(self.config.path)
        self.lu = LibvirtUtils(self.config)
        self.proc_list = []
        self.shutdown = multiprocessing.Event()
        self.job_q = multiprocessing.Queue()

        # ignore signals before creating child processes
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)

        self.job_queue_manager_p = multiprocessing.Process(target=self.job_queue_manager)
        self.job_queue_manager_p.start()

        # then set callbacks before main loop.
        signal.signal(signal.SIGTERM, self.shutdown_callback)
        signal.signal(signal.SIGINT, self.shutdown_callback)


        # job_monitor is our producer; it runs in the main thread.
        self.job_monitor()

    def job_queue_manager(self):
        while True:
            try:
                c = self.job_q.get_nowait()
                domain = self.lu.domain_search(c['domain_uuid'])

            except queue.Empty:
                pass
            except BrokenPipeError:
                pass
            except LibvirtException:
                pass
            else:
                config = c['config']
                xml = VirtDupXML(config, domain)
                jobuuid = c['jobuuid']
                # todo: snapshot manager needs to accept an event object to detect shutdown signals.
                sm = SnapshotManager(config, xml, jobuuid)
                process = multiprocessing.Process(target=sm.stage_image, name=jobuuid)
                process.start()
                self.proc_list.append(process)
            time.sleep(2)
            if self.shutdown.is_set():
                break

    def shutdown_callback(self, a, b):
        self.shutdown.set()
        self.job_queue_manager_p.join()
        for proc in self.proc_list:
            proc.join()

    def check_for_config_change(self):
        new_mtime = os.path.getmtime(self.config.path)
        if new_mtime > self.config_mtime:
            self.config_mtime = new_mtime
            self.reload_config()
        else:
            time.sleep(2)

    def reload_config(self):
        self.config = Config(self.config.path)
        self.lu.shutdown_callback()
        self.lu = LibvirtUtils(self.config)

    def job_monitor(self):
        """
        looks through configured jobs. If it's time to run a job, put it in the queue.
        handles parsing of cron strings.
        job_monitor keeps state in the running_jobs list, which is a dict formatted as
        {jobuuid: int}, where the int represents the number of loops left to wait before
        considering a job within our 'past' time window to require queueing
        when the int value reaches 0, the jobuuid is removed from the list, and any jobs
        whose timestamp falls within the 'past' window will be started.
        Warning: jobs should not be scheduled more frequently than past value in seconds.
        :return:
        """
        timeout = 2
        past = 20
        # maintain a little state for jobs that are running so we don't start them more than once
        running_jobs = {}
        while True:
            self.check_for_config_change()
            cur_time = int(time.time())
            for domain in self.lu.conn.listAllDomains(0):
                domuuid = domain.UUIDString()
                xml = VirtDupXML(self.config, domain)
                for jobuuid, info in xml.loaded_jobs.items():
                    if jobuuid in running_jobs.keys():
                        running_jobs[jobuuid] -= 1
                        if running_jobs[jobuuid] <= 0:
                            running_jobs.pop(jobuuid)
                    else:
                        cron = croniter(info['schedule'], cur_time)
                        prev = int(cron.get_prev())
                        # look a little in the past: why not?
                        threshold = cur_time - past
                        if prev > threshold:
                            # wait at least one loop longer than the past value (2 because int floors the float)
                            running_jobs[jobuuid] = int(past / timeout) + 2
                            # can't pass C objects (e.g. from libvirt) through queue or objects containing them.
                            # doesn't even raise an error
                            c = {'config': self.config,
                                 'domain_uuid': domuuid,
                                 'jobuuid': jobuuid}
                            self.job_q.put_nowait(c)
            time.sleep(timeout)
            if self.shutdown.is_set():
                break



