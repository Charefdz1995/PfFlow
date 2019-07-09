from mongoengine import *
import re
from netaddr import *
from jinja2 import Environment, FileSystemLoader
from napalm import get_network_driver 
import random

class interface(DynamicDocument):
        interface_name = StringField(required=True)
        interface_address = StringField(required=True)
        interface_prefixlen = IntField(required=True)
        interface_speed = IntField(required = True)
        ingress = BooleanField(default = False)

        def configure_netflow(self):
                output = ""
                env = Environment(loader = FileSystemLoader("."))
                template = env.get_template("netflow_int.j2")
                output = template.render(interface_name = self.interface_name)
                return output



class access(DynamicEmbeddedDocument):
        management_interface = StringField(required = True)
        management_address = StringField(required=True)
        username = StringField(required=True)
        password = StringField(required=True)
        enable_secret = StringField(required=False)

class device(DynamicDocument):
        hostname = StringField(required = False)
        management = EmbeddedDocumentField(access)
        interfaces = ListField(ReferenceField(interface))
        
        def connect(self):
                driver = get_network_driver("ios")
                device = None
                try:
                        device = driver(self.management.management_address,self.management.username,
                                                        self.management.password)
                        device.open()
                except Exception as e:
                        print(e)
                return device

        def get_fqdn(self):
                self.hostname = self.connect().get_facts()['fqdn']


        



        def configure_netflow(self,destination):
                global_output = ""
                interfaces_output = ""

                env = Environment(loader=FileSystemLoader("."))
                template = env.get_template("netflow.j2")
                global_output = template.render(destination = destination,self = self)
                for interface in self.interfaces : 
                        interfaces_output += interface.configure_netflow()

                config = (global_output + interfaces_output)
                connection = self.connect().load_merge_candidate(config)
                connection.commit_config()
                print("device is configured")
                return True




        def configure_ip_sla(self,operation,record):
                env = Environment(loader=FileSystemLoader(NET_CONF_TEMPLATES))
                template = env.get_template("ip_sla.j2")
                output = template.render(operation = operation,record = record)
                config = output.splitlines()
                return self.connect().cli(config)


        def configure_ip_sla_responder(self):
                return self.connect().cli(['ip sla responder'])


        def pull_ip_sla_stats(self,operation):
                jitter_cmd = "show ip sla statistics {} | include Destination to Source Jitter".format(str(operation))
                delay_cmd = "show ip sla statistics {} | include Destination to Source Latency".format(str(operation))
                config = [jitter_cmd,delay_cmd]

                result = self.connect().cli(config)

                jitter = int(re.findall("\+d",result[jitter_cmd])[1])
                delay = int(re.findall("\+d",result[delay_cmd])[1])

                return jitter, delay

        def get_cdp_neighbors(self):
                connection = self.connect()
                neighbors = connection.cli(["show cdp neighbors detail | include Device ID","show cdp neighbors detail | include Interface"])

                neighbor_devices = (neighbors["show cdp neighbors detail | include Device ID"]).splitlines()
                neighbor_interfaces = (neighbors["show cdp neighbors detail | include Interface"]).splitlines()

                cdp_devices = [x[x.find(":")+2:] for x in neighbor_devices]
                cdp_interfaces = [{"from":x[x.find(":")+2:x.find(",")],"to":x[x.find("):")+3:]} for x in neighbor_interfaces]
                res = []
                for i in range(len(cdp_devices)):
                        res.append({"to_device":cdp_devices[i],"interfaces":cdp_interfaces[i]})



                return {self.hostname : res }


        def get_interfaces_index(self):
                interfaces_f={}
                connection = self.connect()
                interfaces_sh = connection.cli(["show snmp mib ifmib ifindex"])
                interfaces_sh_sp = (interfaces_sh["show snmp mib ifmib ifindex"]).splitlines()

                for intf in interfaces_sh_sp:
                        parts = intf.split(': ')
                        interfaces_f.update({parts[0]: parts[1].strip('Ifindex = ')})
                print(interfaces_f)




class link(DynamicDocument):
        from_device = ReferenceField(device)
        from_interface =  ReferenceField(interface)
        to_device = ReferenceField(device)
        to_interface = ReferenceField(interface)
        link_speed = IntField(required = False)

        def calculate_speed(self):
                if (self.from_interface.interface_speed >= self.to_interface.interface_speed):
                        self.link_speed = self.to_interface.interface_speed
                elif (self.to_interface.interface_speed >= self.from_interface.interface_speed):
                        self.link_speed = self.from_interface.interface_speed


        def compare(self,clink):
                if(self.from_device == clink.from_device and self.from_interface == clink.from_interface and self.to_device == clink.to_device and self.to_interface == clink.to_interface ):
                        return True
                elif(self.from_device == clink.to_device  and self.from_interface == clink.to_interface and self.to_device == clink.from_device and self.to_interface == clink.from_interface):
                        return True 
                else:
                        return False 




class topology(DynamicDocument):
        topology_name = StringField(required=True)
        topology_desc = StringField(required=False)
        devices = ListField(ReferenceField(device))
        links = ListField(ReferenceField(link)) 


        def get_ip_sla_devices(self,record):
                src_ip = IPAddress(record.IPV4.SRC.ADDR) 
                dst_ip = IPAddress(record.IPV4.DST.ADDR)
                src_device = None
                dst_device = None  
                for device in self.devices:
                        for interface in device.interfaces:
                                network = IPNetwork(interface.interface_address)
                                network.prefixlen = interface.interface_prefixlen
                                if src_ip in network:
                                        src_device = device
                                if dst_ip in network:
                                        dst_device = device

                return src_device,dst_device


        def get_networks(self):
                for device in self.devices:
                        connection = device.connect()
                        ports = connection.get_interfaces_ip()
                        speeds = connection.get_interfaces()
                        interfaces_list = []
                        for port in ports:
                                port_speed = speeds[port]["speed"]
                                for ip in ports[port]["ipv4"]:
                                        cidr = ports[port]["ipv4"][ip]["prefix_length"]
                                        interface_ins = interface(interface_name = port , interface_address = ip , interface_prefixlen = int(cidr),interface_speed = port_speed)
                                        interface_ins.save()
                                        interfaces_list.append(interface_ins)
                        connection.close()
                        device.update(set__interfaces=interfaces_list)


        def create_links(self):
                for device in self.devices:
                        neighbors = device.get_cdp_neighbors()
                        print(neighbors)
                        neighbors = neighbors[device.hostname]
                        devicef = device
                        link_ins = None
                        for neighbor in neighbors:
                                interfacef = None 
                                devicet = None  
                                interfacet = None 

                                for interface in device.interfaces:
                                        if (interface.interface_name == neighbor["interfaces"]["from"]):
                                                interfacef = interface
                                for d in self.devices:
                                        if (d.hostname == neighbor["to_device"]):
                                                devicet = d


                                for interface in devicet.interfaces:
                                        if (interface.interface_name == neighbor["interfaces"]["to"]):
                                                interfacet = interface

                                if(len(self.links) == 0):
                                    link_ins = link(from_device = devicef , from_interface = interfacef , to_device = devicet,to_interface = interfacet)
                                    link_ins.calculate_speed()
                                    link_ins.save()
                                    self.links.append(link_ins)

                                link_ins = link(from_device = devicef , from_interface = interfacef , to_device = devicet,to_interface = interfacet)
                                exist = False
                                for lk in self.links:
                                    if (lk.compare(link_ins)):
                                        exist = True 
                                        break 
                                if not(exist):
                                        link_ins.calculate_speed()
                                        link_ins.save()
                                        self.links.append(link_ins)

        def configure_ntp(self):
                ntp_master = random.choice(self.devices)
                ntp_master_connection = ntp_master.connect()
                ntp_master_connection.cli(["ntp master"])
                for device in self.devices:
                        if (device != ntp_master):
                                device.connect().cli(["ntp server {}".format(ntp_master.management.management_address)])
        def configure_scp(self):
                for device in self.devices:
                        device.connect().cli(["ip scp server enable"])
        def configure_snmp(self):
                for device in self.devices:
                        device.connect().cli(["snmp-server community public RO","snmp-server community private RW"])


class flow(DynamicDocument):
        ipv4_src_addr = StringField(required = True)
        ipv4_dst_addr = StringField(required = True)
        ipv4_protocol = IntField(required = True)
        transport_src_port = IntField(required = True)
        transport_dst_port = IntField(required = True)
        type_of_service = IntField(required = True)
        application_name = StringField(required = True)
        
        

class netflow_fields(DynamicDocument):

        #Real time information about flow in the monitor. 
        counter_bytes = IntField(required = True)
        counter_pkts = IntField(required = True)
        first_switched = FloatField(required = True)
        last_switched  = FloatField(required = True)
        #QoS parameters
        bandwidth = FloatField(required = False)
        #=======================================
        # Device related Information
        collection_time = StringField(required = True)
        input_int = IntField(required = True)
        output_int = IntField(required = True)
        device = ReferenceField(device)
        flow = ReferenceField(flow)
        #=======================================

class ip_sla(Document):
        operation = SequenceField()
        flow_ref = ReferenceField(flow)
        device_ref = ReferenceField(device)

class ip_sla_info(Document):
        avg_jitter = IntField(required = True)
        avg_delay = IntField(required = True)
        packet_loss = IntField(required = False) # For the moment it is false because i dont know how to get it 
        timestamp = StringField(required = False) # temporary false until see how the netflow is sniffing the timestamp to combine it with ip sla 
        ip_sla_ref = ReferenceField(ip_sla)

class application(Document):
        application_ID = IntField(primary_key = True)
        application_NAME = StringField(required = True)
