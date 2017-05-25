"""Defines the class that manages all scheduler resources"""
from __future__ import unicode_literals

import datetime
import threading

from django.utils.timezone import now

from job.resources import NodeResources
from scheduler.resources.agent import AgentResources

# Amount of time between rolling watermark resets
WATERMARK_RESET_PERIOD = datetime.timedelta(minutes=5)


class ResourceManager(object):
    """This class manages all resources from the cluster nodes. This class is thread-safe."""

    def __init__(self):
        """Constructor
        """

        self._agent_resources = {}  # {Agent ID: AgentResources}
        self._agent_resources_lock = threading.Lock()  # Protects self._agent_resources
        self._last_watermark_reset = None
        self._new_offers = {}  # {Offer ID: ResourceOffer}
        self._new_offers_lock = threading.Lock()  # Protects self._new_offers

    def add_new_offers(self, offers):
        """Adds new resource offers to the manager

        :param offers: The list of new offers to add
        :type offers: [:class:`scheduler.resources.offer.ResourceOffer`]
        """

        with self._new_offers_lock:
            for offer in offers:
                self._new_offers[offer.id] = offer

    def generate_status_json(self, status_dict):
        """Generates the portion of the status JSON that describes the resources

        :param status_dict: The status JSON dict
        :type status_dict: dict
        """

        total_running = NodeResources()
        total_offered = NodeResources()
        total_watermark = NodeResources()

        with self._agent_resources_lock:
            for node_dict in status_dict['nodes']:
                agent_id = node_dict['agent_id']
                if agent_id in self._agent_resources:
                    agent_resources = self._agent_resources[agent_id]
                    agent_resources.generate_status_json(node_dict, total_running, total_offered, total_watermark)

        running_dict = {}
        total_running.generate_status_json(running_dict)
        offered_dict = {}
        total_offered.generate_status_json(offered_dict)
        watermark_dict = {}
        total_watermark.generate_status_json(watermark_dict)
        status_dict['resources'] = {'running': running_dict, 'offered': offered_dict, 'watermark': watermark_dict}

    def lost_agent(self, agent_id):
        """Informs the manager that the agent with the given ID was lost and has gone offline

        :param agent_id: The ID of the lost agent
        :type agent_id: str
        """

        # Remove new offers from the lost agent
        with self._new_offers_lock:
            for offer in self._new_offers.values():
                if offer.agent_id == agent_id:
                    del self._new_offers[offer.id]

        # Remove the lost agent
        with self._agent_resources_lock:
            if agent_id in self._agent_resources:
                del self._agent_resources[agent_id]

    def refresh_agent_resources(self, tasks):
        """Refreshes the agents with the current tasks that are running on them and with the new resource offers that
        have been added to the manager since the last time this method was called

        :param tasks: The current running tasks
        :type tasks: [:class:`job.tasks.base_task.Task`]
        """

        with self._new_offers_lock:
            new_offers = self._new_offers
            self._new_offers = {}

        # Group tasks and new offers by agent ID
        agent_offers = {}  # {Agent ID: [ResourceOffer]}
        agent_tasks = {}  # {Agent ID: [Tasks]}
        for offer in new_offers.values():
            if offer.agent_id not in agent_offers:
                agent_offers[offer.agent_id] = []
            agent_offers[offer.agent_id].append(offer)
        for task in tasks:
            if task.agent_id not in agent_tasks:
                agent_tasks[task.agent_id] = []
            agent_tasks[task.agent_id].append(task)

        when = now()

        with self._agent_resources_lock:
            # Create any new agents if this is their first offer
            for offer in new_offers.values():
                if offer.agent_id not in self._agent_resources:
                    self._agent_resources[offer.agent_id] = AgentResources(offer.agent_id)

            # Refresh agents
            for agent_resources in self._agent_resources.values():
                the_offers = agent_offers[agent_resources.agent_id] if agent_resources.agent_id in agent_offers else []
                the_tasks = agent_tasks[agent_resources.agent_id] if agent_resources.agent_id in agent_tasks else []
                agent_resources.refresh_resources(the_offers, the_tasks)

            # Reset rolling watermarks if period has passed
            if when > self._last_watermark_reset + WATERMARK_RESET_PERIOD:
                for agent_resources in self._agent_resources.values():
                    agent_resources.reset_watermark()
                self._last_watermark_reset = when

    def remove_offers(self, offer_ids):
        """Removes the offers with the given IDs from the manager

        :param offer_ids: The list of IDs of the offers to remove
        :type offer_ids: [str]
        """

        with self._new_offers_lock:
            for offer_id in offer_ids:
                if offer_id in self._new_offers:
                    del self._new_offers[offer_id]

        with self._agent_resources_lock:
            for agent_resources in self._agent_resources.values():
                agent_resources.remove_offers(offer_ids)

    def set_agent_shortages(self, agent_shortages):
        """Sets any resource shortages on the appropriate agents

        :param agent_shortages: Dict where resource shortage is stored by agent ID
        :type agent_shortages: dict
        """

        with self._agent_resources_lock:
            for agent_resources in self._agent_resources.values():
                if agent_resources.agent_id in agent_shortages:
                    agent_resources.set_shortage(agent_shortages[agent_resources.agent_id])
                else:
                    agent_resources.set_shortage()


resource_mgr = ResourceManager()
