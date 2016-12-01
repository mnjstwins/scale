"""The Scale Mesos scheduler"""
from __future__ import unicode_literals

import datetime
import logging
import threading

from django.db import DatabaseError
from django.utils.timezone import now
from mesos.interface import Scheduler as MesosScheduler
from mesos.interface import mesos_pb2

from error.models import Error
from job.execution.running.manager import running_job_mgr
from job.execution.running.tasks.cleanup_task import CLEANUP_TASK_ID_PREFIX
from job.execution.running.tasks.results import TaskResults
from job.models import JobExecution
from job.resources import NodeResources
from mesos_api import utils
from queue.models import Queue
from scheduler.cleanup.manager import cleanup_mgr
from scheduler.initialize import initialize_system
from scheduler.models import Scheduler
from scheduler.node.manager import node_mgr
from scheduler.offer.manager import offer_mgr
from scheduler.offer.offer import ResourceOffer
from scheduler.recon.manager import recon_mgr
from scheduler.status.manager import task_update_mgr
from scheduler.sync.job_type_manager import job_type_mgr
from scheduler.sync.scheduler_manager import scheduler_mgr
from scheduler.sync.workspace_manager import workspace_mgr
from scheduler.threads.db_sync import DatabaseSyncThread
from scheduler.threads.recon import ReconciliationThread
from scheduler.threads.schedule import SchedulingThread
from scheduler.threads.status import StatusUpdateThread
from util.host import HostAddress


logger = logging.getLogger(__name__)


class ScaleScheduler(MesosScheduler):
    """Mesos scheduler for the Scale framework"""

    # Warning threshold for normal callbacks (those with no external calls, e.g. database queries)
    NORMAL_WARN_THRESHOLD = datetime.timedelta(milliseconds=5)

    # Warning threshold for callbacks that include database queries
    DATABASE_WARN_THRESHOLD = datetime.timedelta(milliseconds=100)

    def __init__(self):
        """Constructor
        """

        self._driver = None
        self._framework_id = None
        self._master_hostname = None
        self._master_port = None

        self._db_sync_thread = None
        self._recon_thread = None
        self._scheduling_thread = None
        self._status_thread = None

    def registered(self, driver, frameworkId, masterInfo):
        """
        Invoked when the scheduler successfully registers with a Mesos master.
        It is called with the frameworkId, a unique ID generated by the
        master, and the masterInfo which is information about the master
        itself.

        See documentation for :meth:`mesos_api.mesos.Scheduler.registered`.
        """

        self._driver = driver
        self._framework_id = frameworkId.value
        self._master_hostname = masterInfo.hostname
        self._master_port = masterInfo.port
        logger.info('Scale scheduler registered as framework %s with Mesos master at %s:%i',
                    self._framework_id, self._master_hostname, self._master_port)

        initialize_system()
        Scheduler.objects.update_master(self._master_hostname, self._master_port)
        scheduler_mgr.update_from_mesos(self._framework_id, HostAddress(self._master_hostname, self._master_port))
        recon_mgr.driver = self._driver

        # Initial database sync
        job_type_mgr.sync_with_database()
        scheduler_mgr.sync_with_database()
        workspace_mgr.sync_with_database()

        # Start up background threads
        self._db_sync_thread = DatabaseSyncThread(self._driver)
        db_sync_thread = threading.Thread(target=self._db_sync_thread.run)
        db_sync_thread.daemon = True
        db_sync_thread.start()

        self._recon_thread = ReconciliationThread()
        recon_thread = threading.Thread(target=self._recon_thread.run)
        recon_thread.daemon = True
        recon_thread.start()

        self._scheduling_thread = SchedulingThread(self._driver, self._framework_id)
        scheduling_thread = threading.Thread(target=self._scheduling_thread.run)
        scheduling_thread.daemon = True
        scheduling_thread.start()

        self._status_thread = StatusUpdateThread()
        status_thread = threading.Thread(target=self._status_thread.run)
        status_thread.daemon = True
        status_thread.start()

        self._reconcile_running_jobs()

    def reregistered(self, driver, masterInfo):
        """
        Invoked when the scheduler re-registers with a newly elected Mesos
        master.  This is only called when the scheduler has previously been
        registered.  masterInfo contains information about the newly elected
        master.

        See documentation for :meth:`mesos_api.mesos.Scheduler.reregistered`.
        """

        self._driver = driver
        self._master_hostname = masterInfo.hostname
        self._master_port = masterInfo.port
        logger.info('Scale scheduler re-registered with Mesos master at %s:%i',
                    self._master_hostname, self._master_port)

        Scheduler.objects.update_master(self._master_hostname, self._master_port)
        scheduler_mgr.update_from_mesos(mesos_address=HostAddress(self._master_hostname, self._master_port))

        # Update driver for background threads
        self._db_sync_thread.driver = self._driver
        recon_mgr.driver = self._driver
        self._scheduling_thread.driver = self._driver

        self._reconcile_running_jobs()

    def disconnected(self, driver):
        """
        Invoked when the scheduler becomes disconnected from the master, e.g.
        the master fails and another is taking over.

        See documentation for :meth:`mesos_api.mesos.Scheduler.disconnected`.
        """

        if self._master_hostname:
            logger.error('Scale scheduler disconnected from the Mesos master at %s:%i',
                         self._master_hostname, self._master_port)
        else:
            logger.error('Scale scheduler disconnected from the Mesos master')

    def resourceOffers(self, driver, offers):
        """
        Invoked when resources have been offered to this framework. A single
        offer will only contain resources from a single slave.  Resources
        associated with an offer will not be re-offered to _this_ framework
        until either (a) this framework has rejected those resources (see
        SchedulerDriver.launchTasks) or (b) those resources have been
        rescinded (see Scheduler.offerRescinded).  Note that resources may be
        concurrently offered to more than one framework at a time (depending
        on the allocator being used).  In that case, the first framework to
        launch tasks using those resources will be able to use them while the
        other frameworks will have those resources rescinded (or if a
        framework has already launched tasks with those resources then those
        tasks will fail with a TASK_LOST status and a message saying as much).

        See documentation for :meth:`mesos_api.mesos.Scheduler.resourceOffers`.
        """

        started = now()

        agent_ids = []
        resource_offers = []
        for offer in offers:
            offer_id = offer.id.value
            agent_id = offer.slave_id.value
            disk = 0
            mem = 0
            cpus = 0
            for resource in offer.resources:
                if resource.name == 'disk':
                    disk = resource.scalar.value
                elif resource.name == 'mem':
                    mem = resource.scalar.value
                elif resource.name == 'cpus':
                    cpus = resource.scalar.value
            resources = NodeResources(cpus=cpus, mem=mem, disk=disk)
            agent_ids.append(agent_id)
            resource_offers.append(ResourceOffer(offer_id, agent_id, resources))

        node_mgr.register_agent_ids(agent_ids)
        offer_mgr.add_new_offers(resource_offers)

        duration = now() - started
        msg = 'Scheduler resourceOffers() took %.3f seconds'
        if duration > ScaleScheduler.NORMAL_WARN_THRESHOLD:
            logger.warning(msg, duration.total_seconds())
        else:
            logger.debug(msg, duration.total_seconds())

    def offerRescinded(self, driver, offerId):
        """
        Invoked when an offer is no longer valid (e.g., the slave was lost or
        another framework used resources in the offer.) If for whatever reason
        an offer is never rescinded (e.g., dropped message, failing over
        framework, etc.), a framwork that attempts to launch tasks using an
        invalid offer will receive TASK_LOST status updats for those tasks.

        See documentation for :meth:`mesos_api.mesos.Scheduler.offerRescinded`.
        """

        started = now()

        offer_id = offerId.value
        offer_mgr.remove_offers([offer_id])

        duration = now() - started
        msg = 'Scheduler offerRescinded() took %.3f seconds'
        if duration > ScaleScheduler.NORMAL_WARN_THRESHOLD:
            logger.warning(msg, duration.total_seconds())
        else:
            logger.debug(msg, duration.total_seconds())

    def statusUpdate(self, driver, status):
        """
        Invoked when the status of a task has changed (e.g., a slave is lost
        and so the task is lost, a task finishes and an executor sends a
        status update saying so, etc.) Note that returning from this callback
        acknowledges receipt of this status update.  If for whatever reason
        the scheduler aborts during this callback (or the process exits)
        another status update will be delivered.  Note, however, that this is
        currently not true if the slave sending the status update is lost or
        fails during that time.

        See documentation for :meth:`mesos_api.mesos.Scheduler.statusUpdate`.
        """

        started = now()

        task_id = status.task_id.value
        if status.state == mesos_pb2.TASK_LOST:
            logger.warning('Status update for task %s: %s', task_id, utils.get_status_state(status))
        else:
            logger.info('Status update for task %s: %s', task_id, utils.get_status_state(status))

        # Since we have a status update for this task, remove it from reconciliation set
        recon_mgr.remove_task_id(task_id)

        if task_id.startswith(CLEANUP_TASK_ID_PREFIX):
            # Handle status update for cleanup task
            update = utils.create_task_status_update(status)
            cleanup_mgr.handle_task_update(update)
        else:
            # Handle status update for job execution task
            task_update_mgr.add_status_update(status)
            job_exe_id = JobExecution.get_job_exe_id(task_id)

            try:
                running_job_exe = running_job_mgr.get_job_exe(job_exe_id)

                if running_job_exe:
                    results = TaskResults(task_id)
                    results.exit_code = utils.parse_exit_code(status)
                    results.when = utils.get_status_timestamp(status)
                    # Apply status update to running job execution
                    if status.state == mesos_pb2.TASK_RUNNING:
                        running_job_exe.task_start(task_id, results.when)
                    elif status.state == mesos_pb2.TASK_FINISHED:
                        if results.exit_code is None:
                            results.exit_code = 0
                        running_job_exe.task_complete(results)
                    elif status.state == mesos_pb2.TASK_LOST:
                        running_job_exe.task_lost(task_id)
                    elif status.state in [mesos_pb2.TASK_ERROR, mesos_pb2.TASK_FAILED, mesos_pb2.TASK_KILLED]:
                        running_job_exe.task_fail(results)

                    # Remove finished job execution
                    if running_job_exe.is_finished():
                        running_job_mgr.remove_job_exe(job_exe_id)
                        cleanup_mgr.add_job_execution(running_job_exe)
                else:
                    # Scheduler doesn't have any knowledge of this job execution
                    Queue.objects.handle_job_failure(job_exe_id, now(), [],
                                                     Error.objects.get_builtin_error('scheduler-lost'))
            except Exception:
                logger.exception('Error handling status update for job execution: %s', job_exe_id)
                # Error handling status update, add task so it can be reconciled
                recon_mgr.add_task_ids([task_id])

        duration = now() - started
        msg = 'Scheduler statusUpdate() took %.3f seconds'
        if duration > ScaleScheduler.DATABASE_WARN_THRESHOLD:
            logger.warning(msg, duration.total_seconds())
        else:
            logger.debug(msg, duration.total_seconds())

    def frameworkMessage(self, driver, executorId, slaveId, message):
        """
        Invoked when an executor sends a message. These messages are best
        effort; do not expect a framework message to be retransmitted in any
        reliable fashion.

        See documentation for :meth:`mesos_api.mesos.Scheduler.frameworkMessage`.
        """

        started = now()

        agent_id = slaveId.value
        node = node_mgr.get_node(agent_id)

        if node:
            logger.info('Message from %s on host %s: %s', executorId.value, node.hostname, message)
        else:
            logger.info('Message from %s on agent %s: %s', executorId.value, agent_id, message)

        duration = now() - started
        msg = 'Scheduler frameworkMessage() took %.3f seconds'
        if duration > ScaleScheduler.NORMAL_WARN_THRESHOLD:
            logger.warning(msg, duration.total_seconds())
        else:
            logger.debug(msg, duration.total_seconds())

    def slaveLost(self, driver, slaveId):
        """
        Invoked when a slave has been determined unreachable (e.g., machine
        failure, network partition.) Most frameworks will need to reschedule
        any tasks launched on this slave on a new slave.

        See documentation for :meth:`mesos_api.mesos.Scheduler.slaveLost`.
        """

        started = now()

        agent_id = slaveId.value
        node = node_mgr.get_node(agent_id)

        if node:
            logger.error('Node lost on host %s', node.hostname)
        else:
            logger.error('Node lost on agent %s', agent_id)

        node_mgr.lost_node(agent_id)
        offer_mgr.lost_node(agent_id)

        # Fail job executions that were running on the lost node
        if node:
            for running_job_exe in running_job_mgr.get_job_exes_on_node(node.id):
                try:
                    running_job_exe.execution_lost(started)
                except DatabaseError:
                    logger.exception('Error failing lost job execution: %s', running_job_exe.id)
                    # Error failing execution, add task so it can be reconciled
                    task = running_job_exe.current_task
                    if task:
                        recon_mgr.add_task_ids([task.id])
                if running_job_exe.is_finished():
                    running_job_mgr.remove_job_exe(running_job_exe.id)
                    cleanup_mgr.add_job_execution(running_job_exe)

        duration = now() - started
        msg = 'Scheduler slaveLost() took %.3f seconds'
        if duration > ScaleScheduler.DATABASE_WARN_THRESHOLD:
            logger.warning(msg, duration.total_seconds())
        else:
            logger.debug(msg, duration.total_seconds())

    def executorLost(self, driver, executorId, slaveId, status):
        """
        Invoked when an executor has exited/terminated. Note that any tasks
        running will have TASK_LOST status updates automatically generated.

        See documentation for :meth:`mesos_api.mesos.Scheduler.executorLost`.
        """

        started = now()

        agent_id = slaveId.value
        node = node_mgr.get_node(agent_id)

        if node:
            logger.error('Executor %s lost on host: %s', executorId.value, node.hostname)
        else:
            logger.error('Executor %s lost on agent: %s', executorId.value, agent_id)

        duration = now() - started
        msg = 'Scheduler executorLost() took %.3f seconds'
        if duration > ScaleScheduler.NORMAL_WARN_THRESHOLD:
            logger.warning(msg, duration.total_seconds())
        else:
            logger.debug(msg, duration.total_seconds())

    def error(self, driver, message):
        """
        Invoked when there is an unrecoverable error in the scheduler or
        scheduler driver.  The driver will be aborted BEFORE invoking this
        callback.

        See documentation for :meth:`mesos_api.mesos.Scheduler.error`.
        """

        logger.error('Unrecoverable error: %s', message)

    def shutdown(self):
        """Performs any clean up required by this scheduler implementation.

        Currently this method just notifies any background threads to break out of their work loops.
        """

        logger.info('Scheduler shutdown invoked, stopping background threads')
        self._db_sync_thread.shutdown()
        self._recon_thread.shutdown()
        self._scheduling_thread.shutdown()
        self._status_thread.shutdown()

    def _reconcile_running_jobs(self):
        """Looks up all currently running jobs in the database and sets them up to be reconciled with Mesos"""

        # List of task IDs to reconcile
        task_ids = []

        # Query for job executions that are running
        job_exes = JobExecution.objects.get_running_job_exes()

        # Find current task IDs for running executions
        for job_exe in job_exes:
            running_job_exe = running_job_mgr.get_job_exe(job_exe.id)
            if running_job_exe:
                task = running_job_exe.current_task
                if task:
                    task_ids.append(task.id)
            else:
                # Fail any executions that the scheduler has lost
                Queue.objects.handle_job_failure(job_exe.id, now(), [],
                                                 Error.objects.get_builtin_error('scheduler-lost'))

        # Send task IDs to reconciliation thread
        recon_mgr.add_task_ids(task_ids)
