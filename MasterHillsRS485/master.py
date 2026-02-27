# -*- coding: utf-8 -*-
import os
import os.path
import socket
import time
import sys
import threading
import json
import logging
import logging.config
import logging.handlers
import collections
import paho.mqtt.client as mqtt

SW_VERSION = 'Masterhills SmartHome v1.0'

class RS485Sock(socket.socket):
    def __init__(self, *args, **kwargs):
        super(RS485Sock, self).__init__(*args, **kwargs)
        self._send_lock = threading.Lock()

    def connect(self):
        self.settimeout(10)
        try:
            super(RS485Sock, self).connect((config['RS485_IP'], config['RS485_PORT']))
        except Exception as e:
            logging.warning('Failed to connect RS485 socket.[{}][{}:{}]'.format(e, config['RS485_IP'], config['RS485_PORT']))
            return False
        logging.info('Connected to RS485 socket.')
        self.settimeout(None)
        return True

    def recv(self):
        while True:
            try:
                data = super(RS485Sock, self).recv(1)
                return data
            except Exception as e:
                logging.error("RS485 socket receive error. :" + str(e))
                logging.warning("Try Reconnect")
                time.sleep(5)
                self.connect()

    def send(self, data):
        with self._send_lock:
            while True:
                try:
                    super(RS485Sock, self).send(data)
                    return
                except Exception as e:
                    logging.error("RS485 socket send error. :" + str(e))
                    logging.info("Try Reconnect")
                    time.sleep(5)
                    self.connect()


class Device():
    def __init__(self, name):
        self.device_name = name
        self.device_type = 'NA'
        self.state_command = ''

    def __str__(self):
        return self.device_type + '-' + self.device_name

    def SetGroupName(self, group_name):
        self.group_name = group_name

    def SetStateFromMQTT(self, topic, payload):
        self.SetMQTTState(payload)
        self.Send485(payload)

    def SetMQTTState(self, state):
        mqttc.publish(self.state_command, state)

    def ProcessPacket(self, packet):
        self.CheckProcess(packet)


class SwitchDevice(Device):
    def __init__(self, name, icon):
        super().__init__(name)
        self.device_type = 'switch'
        self.icon = icon

class LightDevice(Device):
    def __init__(self, light_group, index):
       super().__init__('light' + str(index))
       self.device_type = 'light'
       self.index = index
       self.state_on = False
       self.light_group = light_group

    def Send485(self, payload):

        # don't send 485 packet when the payload and current state are the same
        # this is because HA (maybe) send state packet from time to time
        # if we send 485 packet in such a case, the light works incorrently.
        with self.light_group._state_lock:
            if payload == 'on':
                if self.state_on == False:
                    self.state_on = True
                    logging.info("light send485 on")
                    self.light_group.Send485Group()
            elif payload == 'off':
                if self.state_on == True:
                    self.state_on = False
                    logging.info("light send485 off")
                    self.light_group.Send485Group()
            else:
                logging.warning('Unknown payload : '+ payload)
                return

class ThermostatDevice(Device):
    def __init__(self, thermostat_group):
       super().__init__('thermostat')
       self.device_type = 'climate'
       self.thermostat_group = thermostat_group
       self.temp_command = ''
       self.mode = 'off'
       self.action = 'off'
       self.target_temp = 23
       self.current_temp = 20

    def Send485(self):

        self.thermostat_group.Send485Group(self)

    def SetStateFromMQTT(self, topic, payload):
        logging.info('Thermostat SetStateFromMQTT :' + topic)

        token = topic.split('/')
        if token[4] == 'target_temp':
            self.SetTargetTemp(int(float(payload)))
        elif token[4] == 'mode':
            self.SetMode(payload)
        self.Send485()

        # get state in 10 sec. becuase
        # the instant replay cannot reflect current heating state
        threading.Timer(10.0, self.thermostat_group.Send485GetCurrent).start()

    def SendCurrentStateToMQTT(self):
        payload = {
            'mode': self.mode,
            'act': self.action,
            'target_temp': self.target_temp,
            'current_temp': self.current_temp
        }
        logging.info("Send MQTT Thermostat {} : {}".format(self.thermostat_group.group_name, str(payload)))
        mqttc.publish(self.state_command, json.dumps(payload))

    def SetTargetTemp(self, target_temp):
        self.target_temp = target_temp

    def SetMode(self, payload):
        self.mode = payload

    def SetAction(self, heating, cooling):
        if self.mode == 'off':
            self.action = 'off'
        else:
            if heating:
                self.action = 'heating'
            elif cooling:
                self.action = 'cooling'
            else:
                self.action = 'idle'

    def SetCurrentTemp(self, current_temp):
        self.current_temp = current_temp


class ElevatorDevice(SwitchDevice):
    def __init__(self,):
        super().__init__('elevator', 'mdi:elevator')

    def CheckProcess(self, packet):
        if packet.is_send_type():
            part = packet.get_dev_part()

            # call elevator from wallswitch
            if part == '01004400':
                self.SetMQTTState('on')

            # elevator arrived!
            if part == '44000100':
                self.SetMQTTState('off')

    def Send485(self, payload):
        # only works for call elevator
        if payload == 'on':
            rs485_sock.send(bytearray.fromhex('AA5530BC0001004400010000000000000000320D0D'))


class AllLightsDevice(SwitchDevice):
    def __init__(self,):
        super().__init__('alllights', 'mdi:lightbulb-group')

    def CheckProcess(self, packet):
        if packet.is_notify_type():
            part = packet.get_dev_part()

            # all lights command from wallswitch
            if part == '0eff0100':
                cmd = packet.get_dev_command()
                if cmd == '65':
                    self.SetMQTTState('off')
                elif cmd == '66':
                    self.SetMQTTState('on')

    def Send485(self, payload):
        # only works for call elevator
        if payload == 'on':
            rs485_sock.send(bytearray.fromhex('AA55309C000EFF010066FFFFFFFFFFFFFFFF380D0D'))
        elif payload == 'off':
            rs485_sock.send(bytearray.fromhex('AA55309C000EFF01006500000000000000003F0D0D'))




class DeviceGroup():
    def __init__(self, name):
        self.group_name = name
        self.device_list = []

    def AddDevice(self, dev):
        dev.SetGroupName(self.group_name)
        self.device_list.append(dev)

    def GetDevice(self, device_type, device_name):
        for dev in self.device_list:
            if dev.device_type == device_type and dev.device_name == device_name:
                return dev
        return None

    def ProcessPacket(self, packet):
        if hasattr(self, 'ProcessGroupPacket'):
            self.ProcessGroupPacket(packet)
        else:
            for dev in self.device_list:
                dev.ProcessPacket(packet)

    def GetCurrentState(self):
        pass

    def __str__(self):
        return self.group_name + ' ==> ' + ",".join([str(item) for item in self.device_list])

    def AppendMQTTListDevice(self, prefix, subscribe_list, publish_list):
        for dev in self.device_list:
            ha_topic = '{}/{}/{}/{}/config'.format( \
                prefix, dev.device_type, self.group_name, dev.device_name)
            dev.state_command = '{}/{}/{}/{}/state'.format( \
                prefix, dev.device_type, self.group_name, dev.device_name)
            ha_payload = {
                'name': '{}_{}'.format(self.group_name, dev.device_name),
                'cmd_t': '{}/{}/{}/{}/set'.format(prefix, dev.device_type, self.group_name, dev.device_name),
                'stat_t': dev.state_command,
                'command_on_template': 'on',
                'payload_on': 'on',
                'command_off_template': 'off',
                'payload_off': 'off',
                'uniq_id': '{}_{}'.format(self.group_name, dev.device_name),
                'device': {
                    'name': 'MS {}'.format(dev.device_name),
                    'ids': 'MS_{}'.format(dev.device_name),
                    'mf': 'SmartHome',
                    'mdl': 'VirtualDev 1.0',
                    'sw': SW_VERSION
                }
            }
            if hasattr(dev, 'icon'):
                ha_payload['ic'] = dev.icon
            subscribe_list.append((ha_payload['cmd_t'], 0))
            publish_list.append({ha_topic : json.dumps(ha_payload)})

class DeviceThermostatGroup(DeviceGroup):
    def __init__(self, name, packet_id, cool_packet_id):

        super().__init__(name)
        self.packet_id = packet_id
        self.cool_packet_id = cool_packet_id

        self.AddDevice(ThermostatDevice(self))


    def ProcessGroupPacket(self, packet):
        if packet.get_dev_part() == self.packet_id and packet.get_dev_command() == '00':
            values = packet.get_value()

            logging.debug("HEAT payload: "+ str(values))

            # Termostat group has only one device
            for dev in self.device_list:
                # don't do anything about heater when AC is on
                if dev.mode == 'cool':
                    break

                if values[0:4] == '1100':
                    dev.SetMode('heat')
                    dev.SetTargetTemp(int(values[4:6], 16))
                elif values[0:4] == '1101':
                    dev.SetMode('off')
                else:
                    logging.info('Unknown mode : ' + values)

                dev.SetCurrentTemp(int(values[8:10], 16))
                dev.SetAction(True if values[14:16] == '01' else False, False)
                dev.SendCurrentStateToMQTT()
        elif packet.get_dev_part() == self.cool_packet_id and packet.get_dev_command() == '00':
            values = packet.get_value()

            logging.debug("COOL payload: "+ str(values))

            for dev in self.device_list:
                # don't do anything about AC when heater is on
                if dev.mode == 'heat':
                    break

                if values[0:2] == '10':
                    dev.SetMode('cool')
                    dev.SetAction(False, True)
                else:
                    dev.SetMode('off')
                    dev.SetAction(False, False)
                dev.SetCurrentTemp(int(values[8:10], 16))
                dev.SetTargetTemp(int(values[10:12], 16))
                dev.SendCurrentStateToMQTT()

    def AppendMQTTListDevice(self, prefix, subscribe_list, publish_list):

        for dev in self.device_list:
            ha_topic = '{}/{}/{}/{}/config'.format( \
                prefix, dev.device_type, self.group_name, dev.device_name)

            dev.temp_command = '{}/{}/{}/{}/target_temp'.format( \
                prefix, dev.device_type, self.group_name, dev.device_name)

            dev.state_command = '{}/{}/{}/{}/state'.format( \
                prefix, dev.device_type, self.group_name, dev.device_name)

            ha_payload = {
                'name': '{}_{}'.format(self.group_name, dev.device_name),
                # 'away_mode_cmd_t': '{}/{}/{}/{}/away'.format(prefix, dev.device_type, self.group_name, dev.device_name),
                'action_topic': dev.state_command,
                'action_template': '{{ value_json.act }}',
                'mode_cmd_t': '{}/{}/{}/{}/mode'.format(prefix, dev.device_type, self.group_name, dev.device_name),
                'mode_stat_t': dev.state_command,
                'mode_stat_tpl': '{{ value_json.mode }}',
                'temp_cmd_t': dev.temp_command,
                'temp_stat_t': dev.state_command,
                'temp_stat_tpl': '{{ value_json.target_temp }}',
                'curr_temp_t': dev.state_command,
                'curr_temp_tpl': '{{ value_json.current_temp }}',
                'min_temp': 10,
                'max_temp': 40,
                'temp_step': 1,
                # 'modes': ['off', 'heat', 'fan_only'],
                'modes': ['off', 'cool', 'heat'],
                'uniq_id': '{}_{}'.format(self.group_name, dev.device_name),
                'device': {
                    'name': 'MS {}'.format(dev.device_name),
                    'ids': 'MS_{}'.format(dev.device_name),
                    'mf': 'SmartHome',
                    'mdl': 'VirtualDev 1.0',
                    'sw': SW_VERSION
                }
            }

            if hasattr(dev, 'icon'):
                ha_payload['ic'] = dev.icon
            subscribe_list.append((ha_payload['mode_cmd_t'], 0))
            subscribe_list.append((ha_payload['temp_cmd_t'], 0))
            publish_list.append({ha_topic : json.dumps(ha_payload)})

    def Send485Group(self, dev):

        logging.debug("Thermo send 486 mode : {}".format(dev.mode))

        if dev.mode != 'cool':
            # send command for heater
            value = ''

            if dev.mode == 'heat':
                value += '1100'
            elif dev.mode == 'off':
                value += '1101'

            if len(value) > 0:
                value += '{0:02x}0000000000'.format( dev.target_temp )

                p = MSPacket()
                p.GeneratePacket(self.packet_id, value)

                rs485_sock.send(bytearray.fromhex(p.get_packet()))


            time.sleep(1)

        if dev.mode != 'heat':
            # send command for cooler
            value = ''

            if dev.mode == 'cool':
                dev.target_temp = 28
                value += '10'
            elif dev.mode == 'off':
                value += '00'

            if len(value) > 0:
                value += '00000000{0:02x}0000'.format( dev.target_temp )

                p = MSPacket()
                p.GeneratePacket(self.cool_packet_id, value)

                logging.debug("485 PACKET : {}".format(p.get_packet()))

                rs485_sock.send(bytearray.fromhex(p.get_packet()))



    def Send485GetCurrent(self):
        p = MSPacket()
        p.GeneratePacket(self.packet_id, '0000000000000000', '3a')
        ret = rs485_sock.send(bytearray.fromhex(p.get_packet()))

        time.sleep(1)
        p = MSPacket()
        p.GeneratePacket(self.cool_packet_id, '0000000000000000', '3a')
        ret = rs485_sock.send(bytearray.fromhex(p.get_packet()))

    def GetCurrentState(self):
        time.sleep(1)
        self.Send485GetCurrent()




class DeviceLightGroup(DeviceGroup):
    def __init__(self, name, num, packet_id):
        super().__init__(name)
        self.lights_num = num
        self.packet_id = packet_id
        self._state_lock = threading.Lock()

        for n in range(1, num + 1):
            self.AddDevice(LightDevice(self, n))

    def Send485Group(self):
        value = ''
        for dev in self.device_list:
            if dev.state_on:
                value += 'ff'
            else:
                value += '00'

        append_len = 16 - len(value)
        for x in range(append_len):
            value += '0'

        logging.info("Send MQTT Light {} : {}".format(self.group_name, value))
        p = MSPacket()
        p.GeneratePacket(self.packet_id, value)
        rs485_sock.send(bytearray.fromhex(p.get_packet()))

    def ProcessGroupPacket(self, packet):
        if packet.get_dev_part() == self.packet_id:
            # skip echoes of our own commands (type 30b)
            if packet.is_send_type():
                return
            with self._state_lock:
                values = packet.get_value()
                for num, dev in enumerate(self.device_list):
                    if values[num*2:num*2+2] == 'ff':
                        dev.state_on = True
                        dev.SetMQTTState('on')
                    else:
                        dev.state_on = False
                        dev.SetMQTTState('off')

class Home():
    def __init__(self):
        self.group_list = []
        self.prefix = 'homeassistant'

    def AddDeviceGroup(self, group):
        self.group_list.append(group)

    def __str__(self):
        return "#".join([str(item) for item in self.group_list])

    def AppendMQTTList(self, subscribe_list, publish_list):
        for group in self.group_list:
            group.AppendMQTTListDevice(self.prefix, subscribe_list, publish_list)

    def GetDeviceFromMQTT(self, topic):
        token = topic.split('/')
        if token[0] != self.prefix:
            return None

        for group in self.group_list:
            if group.group_name == token[2]:
                dev = group.GetDevice(token[1], token[3])
                if dev is not None:
                    return dev

        return None

    def ProcessPacket(self, packet):
        if packet.is_valid_packet():
            for group in self.group_list:
                group.ProcessPacket(packet)
        else:
            logging.info("invalid packet")

    def GetCurrentDeviceStates(self):
        for group in self.group_list:
            group.GetCurrentState()


class MSPacket():
    def __init__(self):
        self.p = {}

    def SetPacket(self, packet):
        self.p['prefix'] = packet[:4].lower()
        self.p['type'] = packet[4:7].lower()
        self.p['order'] = packet[7:8].lower()
        self.p['pad'] = packet[8:10].lower()
        self.p['device1'] = packet[10:12].lower()
        self.p['room1'] = packet[12:14].lower()
        self.p['device2'] = packet[14:16].lower()
        self.p['room2'] = packet[16:18].lower()
        self.p['command'] = packet[18:20].lower()
        self.p['value'] = packet[20:36].lower()
        self.p['checksum'] = packet[36:38].lower()
        self.p['suffix'] = packet[38:42].lower()

    def GeneratePacket(self, target_dev, value, command = '00'):
        self.p['prefix'] = 'aa55'
        self.p['type'] = '30b'
        self.p['order'] = 'c'
        self.p['pad'] = '00'
        self.p['device1'] = target_dev[:2].lower()
        self.p['room1'] = target_dev[2:4].lower()
        self.p['device2'] = target_dev[4:6].lower()
        self.p['room2'] = target_dev[6:8].lower()
        self.p['command'] = command
        self.p['value'] = value
        self.p['checksum'] = self.calc_checksum()
        self.p['suffix'] = '0d0d'


    def __str__(self):
        return str(self.p)

    def calc_checksum(self):
        body =  self.get_body()
        sum_packet = sum(bytearray.fromhex(body)[:18])
        chk_sum = '{0:02x}'.format((sum_packet + 1) % 256)
        return chk_sum

    def is_valid_checksum(self):
        return self.p['checksum'] == self.calc_checksum()

    def get_body(self):
        return self.p['prefix'] +  self.p['type']  +  self.p['order'] + \
            self.p['pad'] +  self.p['device1'] + self.p['room1'] +  \
            self.p['device2']  + self.p['room2']  + self.p['command'] + self.p['value']

    def get_dev_part(self):
        return self.p['device1'] + self.p['room1'] +  \
            self.p['device2']  + self.p['room2']

    def get_dev_command(self):
        return self.p['command']

    def is_valid_packet(self):
        if self.p['prefix'] != 'aa55' or self.p['suffix'] != '0d0d':
            return False
        return self.is_valid_checksum()

    def is_send_type(self):
        if self.p['type'] == '30b':
            return True

    def is_notify_type(self):
        if self.p['type'] == '309':
            return True

    def get_value(self):
        return self.p['value']

    def get_packet(self):
        return self.get_body() + self.p['checksum'] + self.p['suffix']



class Daemon():
    def __init__(self):
        self._name = 'kocom'
        self.AddHomeDevices()

    def AddHomeDevices(self):
        self.home = Home()

        wallpad = DeviceGroup('wallpad')
        self.home.AddDeviceGroup(wallpad)

        wallpad.AddDevice(ElevatorDevice())
        wallpad.AddDevice(AllLightsDevice())

        kitchen = DeviceLightGroup('kitchen', 3, '0e040100')
        self.home.AddDeviceGroup(kitchen)

        livingroom = DeviceLightGroup('livingroom', 3, '0e000100')
        self.home.AddDeviceGroup(livingroom)

        bedroom = DeviceLightGroup('bedroom', 2, '0e010100')
        self.home.AddDeviceGroup(bedroom)

        studyroom = DeviceLightGroup('studyroom', 3, '0e020100')
        self.home.AddDeviceGroup(studyroom)

        guestroom = DeviceLightGroup('guestroom', 2, '0e030100')
        self.home.AddDeviceGroup(guestroom)

        livingroom = DeviceThermostatGroup('livingroom', '36000100', '39000100')
        self.home.AddDeviceGroup(livingroom)

        bedroom = DeviceThermostatGroup('bedroom', '36010100', '39010100')
        self.home.AddDeviceGroup(bedroom)

        studyroom = DeviceThermostatGroup('studyroom', '36020100', '39020100')
        self.home.AddDeviceGroup(studyroom)

        guestroom = DeviceThermostatGroup('guestroom', '36030100', '39030100')
        self.home.AddDeviceGroup(guestroom)


    def init(self):

        global mqttc
        global rs485_sock
        mqttc = mqtt.Client()
        rs485_sock = RS485Sock()

        mqttc.on_message = self.on_message
        mqttc.on_connect = self.on_connect
        mqttc.on_disconnect = self.on_disconnect

        mqttc.username_pw_set(username=config['MQTT_USER'], password=config['MQTT_PASSWD'])

        try:
            mqttc.connect(config['MQTT_IP'], 1883, 60)
        except Exception as e:
            logging.error('Failed to connect MQTT: {}'.format(e))
            return False
        mqttc.loop_start()

        if rs485_sock.connect() == False:
            return False

        t = threading.Thread(target = self.state_loop)
        t.start()
        return True


    def state_loop(self):
        while True:
            self.home.GetCurrentDeviceStates()
            time.sleep( 5 * 60 )

    def on_message(self, client, obj, msg):
        logging.info('on_message : ' + msg.topic)
        device = self.home.GetDeviceFromMQTT(msg.topic)
        if device is None:
            logging.warning('Unknown topic : ' + msg.topic)
        else:
            payload = msg.payload.decode()
            logging.debug("[MQTT] payload: "+ str(payload))
            device.SetStateFromMQTT(msg.topic, payload)


    def on_connect(self, client, userdata, flags, rc):
        if int(rc) == 0:
            logging.info("[MQTT] connected OK")
            self.setup_mqtt_subscribe()
        elif int(rc) == 1:
            logging.info("[MQTT] 1: Connection refused – incorrect protocol version")
        elif int(rc) == 2:
            logging.info("[MQTT] 2: Connection refused – invalid client identifier")
        elif int(rc) == 3:
            logging.info("[MQTT] 3: Connection refused – server unavailable")
        elif int(rc) == 4:
            logging.info("[MQTT] 4: Connection refused – bad username or password")
        elif int(rc) == 5:
            logging.info("[MQTT] 5: Connection refused – not authorised")
        else:
            logging.info("[MQTT] {} : Connection refused".format(rc))


    def on_disconnect(self, client, userdata, rc):
        logging.info("[MQTT] Disconnected! : " + str(rc))

    def setup_mqtt_subscribe(self):
        subscribe_list = []
        publish_list = []
        subscribe_list.append(('rs485/bridge/#', 0))

        self.home.AppendMQTTList(subscribe_list, publish_list)

        logging.debug(subscribe_list)
        logging.debug(publish_list)
        mqttc.subscribe(subscribe_list)
        for ha in publish_list:
            for topic, payload in ha.items():
                mqttc.publish(topic, payload)

    def check_sum(self, packet):
        sum_packet = sum(bytearray.fromhex(packet)[:17])
        v_sum = int(packet[34:36], 16) if len(packet) >= 36 else 0
        chk_sum = '{0:02x}'.format((sum_packet + 1 + v_sum) % 256)
        orgin_sum = packet[36:38] if len(packet) >= 38 else ''
        return (True, chk_sum) if chk_sum == orgin_sum else (False, chk_sum)


    def packet_process(self, rawdata):
        p = MSPacket()
        p.SetPacket(rawdata)
        self.home.ProcessPacket(p)


    def read_rs485(self):
        packet = ''
        start_flag = False
        while True:
            raw_data = rs485_sock.recv()

            hex_d = raw_data.hex()
            start_hex = 'aa'
            if hex_d == start_hex:
                start_flag = True
            if start_flag:
                packet += hex_d

            if len(packet) >= 42:
                chksum = self.check_sum(packet)
                if chksum[0]:
                    self.tick = time.time()
                    logging.debug("[Packet received] {}".format(packet))
                    self.packet_process(packet)
                packet = ''
                start_flag = False

    def run(self):

        self.read_rs485()




if __name__ == '__main__':

    root_dir = str(os.path.dirname(os.path.realpath(__file__)))
    conf_path = sys.argv[1]
    log_dir = root_dir + '/log/'
    if not os.path.isdir(log_dir):
        os.mkdir(log_dir)

    try:
        with open(conf_path, 'r') as f:
            config = json.load(f, object_pairs_hook=collections.OrderedDict)
    except FileNotFoundError as e:
        print("[ERROR] Cannot open configuration file")
        sys.exit(1)

    log_path = str(log_dir + '/addon.log')

    if config['LOG_LEVEL'] == "debug": level = logging.DEBUG
    elif config['LOG_LEVEL'] == "warn": level = logging.WARN
    else: level = logging.INFO

    format   = '%(asctime)s : %(message)s - LN %(lineno)s'
    handlers = [logging.handlers.RotatingFileHandler(filename = log_path, maxBytes = 10*1024*1024, backupCount = 10, encoding = 'utf-8'), logging.StreamHandler()]

    logging.basicConfig(level = level, format = format, handlers = handlers)

    daemon = Daemon()
    if daemon.init() == False:
        logging.warning("Initilization failed.")
    else:
        daemon.run()
