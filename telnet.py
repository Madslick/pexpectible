#!/bin/python3.6
from optparse import OptionParser
import pexpect
import yaml
from multiprocessing import Pool
from functools import partial
import re
import pdb

import sys
import time

magic_vars = {
    '!ctrl+y!': "\031",
    '!enter!': '\015',
    '!ctrl+c!': '\003'
}

def command_variable_check(switch_name, command, play):
    m = re.search('%.+%', command)
    if m is not None:
        attribute_keys = play.get('custom_command_attributes')
        attr_name = m.group(0).replace('%', '')
        for attr_key in attribute_keys:
            if attr_key['name'] == attr_name:
                for attr in attr_key['switches']:
                    if attr['switch'] == switch_name:
                        return command.replace(m.group(0), attr['attribute'])
    return command

def shouldDeviceSkipPlay(play, switch_name):
    ccas = play.get('custom_command_attributes')
    if ccas is not None:
        for cca_device in ccas[0]['switches']:
            # Switch has a listing in the custom_command_attributes to correctly run the play as intended
            if cca_device['switch'] == switch_name:
                return False
        # Play has custom_command_attribute but this switch isn't included in that
        return True
    # Play does not contain a custom_command_attribute
    return False

def run_play(play, device):
    if shouldDeviceSkipPlay(play, device['name']):
        print("INFO: Skipping %s because this play requires custom_command_attribute(s) that we couldn't find for this device" % device['name'])
        return

    # Instantiate pexpect object
    child = pexpect.spawn("telnet %s %s" % (device['console_ip'], device['console_port']), timeout=timeout)
    child.print_lines = False

    # For logging to a file in append mode (will create file if not already existing)
    fout = open('logs/%s-telnet.log' % device['name'], 'wb')
    child.logfile = fout
    fout.write (b'\n\nExecution datetime: %4d%02d%02d.%02d%02d%02d \n\n' % time.localtime()[:-3])
    print("INFO: Starting Execution on %s" % device['name'])
    # Identify our starting point and handle the situation
    child.sendline('')
    i=child.expect(['Enter your option', '>', '#', pexpect.TIMEOUT])
    # If 'Enter your option' found in switch output from terminal switch
    if i==0:
        child.sendline('1')

    # Start at > prompt
    if i==1:
        pass

    # Start at # prompt
    if i==2:
        child.sendline('\003') # this is ctrl + c
        child.sendline('end')
        child.sendline('exit')
        time.sleep(1)

    if i==3:
        print('ERROR: Switch %s timed out due to an unresponsive connection most likely.' % device['name'])
        return

    # Now we can run any commands
    for instruction in play['steps']:
        command = instruction['command']
        # regular expression search for magic_variables
        m = re.search('!.+!', command)
        if m is not None:
            command_in_parts = command.split(m.group(0))
            if len(command_in_parts) >= 2:
                command = command_in_parts[0] + magic_vars[m.group(0)] + command_in_parts[1]

        # Have the option to send command without line break
        if 'inline' in instruction and instruction['inline'] == True:
            child.send(command_variable_check(device['name'], command, play))
        else:
            child.sendline(command_variable_check(device['name'], command, play))

        # Expect checks
        if 'expect' in instruction:
            expect_phrases = []

            for exp in instruction['expect']:
                expect_phrases.append(exp['phrase'])
            expect_phrases.append(pexpect.TIMEOUT)
            expect_phrases.append(pexpect.EOF)

            # value is an integer correlating to the index of the phrase found first in expect_phrases
            phrase_found = child.expect(expect_phrases, timeout=instruction['expect_wait_time'])

            if phrase_found < len(expect_phrases) - 2: # Then we got timeout or EOF
                phrase = instruction['expect'][phrase_found]
                for step in phrase['steps']:
                    command = step['command']
                    # regular expression search for magic_variables
                    m = re.search('!.+!', command)
                    if m is not None:
                        command_in_parts = command.split(m.group(0))
                        if len(command_in_parts) >= 2:
                            command = command_in_parts[0] + magic_vars[m.group(0)] + command_in_parts[1]
                    # Have the option to send command without line break
                    if 'inline' in step and step['inline'] == True:
                        child.send(command_variable_check(device['name'], command, play))
                    else:
                        child.sendline(command_variable_check(device['name'], command, play))

                    # Wait the instructed amount of seconds
                    if step['wait_time'] is not None:
                        time.sleep(step['wait_time'])

        # Wait the instructed amount of seconds
        if 'wait_time' in instruction:
            time.sleep(instruction['wait_time'])

        #if instruction['expect']:
            # i will be which phrase was caught
            #i = child.expect(instruction['ex'])

    # This expect writes session to the logfile
    try:
        child.expect(pexpect.EOF, timeout=1)
    except Exception as e:
        # Just ignore any exception thrown by this, I find it annoying
        pass

    # Flush logfile, close pexpect session
    child.logfile.flush()
    child.close()

if __name__ == '__main__':

    # Get list of devices and the play to run from the option parser
    inventory_targets = []
    play = ''

    # cli arguments
    parser = OptionParser(usage="usage: %prog [options]", version="%prog 1.0")
    parser.add_option("-i", "--inventory",
        action="store",
        dest="inventory_file",
        default="inventory.yml",
        help="YAML file containing inventory of switches and groups")

    parser.add_option("-g", "--group",
        action="store", # optional because action defaults to "store"
        dest="group",
        default='',
        help="Group of switches to execute a play against from the inventory file")

    parser.add_option("-s", "--single",
        action="store", # optional because action defaults to "store"
        dest="single",
        default='',
        help="Single device to run play on")

    parser.add_option("-b", "--playbook",
        action="store", # optional because action defaults to "store"
        dest="playbook",
        default='playbook.yml',
        help="specify which playbook file to load from")

    parser.add_option("-p", "--play",
        action="store", # optional because action defaults to "store"
        dest="play",
        default='',
        help="specify which play out of the playbook")

    (options, args) = parser.parse_args()

    print("""
********************************************************************************
You have specified the following information with this execution:

Load Inventory File: %s
Load Group: %s
Load Single: %s
Load Playbook: %s
Load Play: %s
********************************************************************************
""" % (options.inventory_file, options.group, options.single, options.playbook, options.play))

    # Grab the groups from the inventory file
    with open(options.inventory_file, 'r') as stream:
        try:
            inventory = yaml.load(stream)
            if options.group:
                inventory_targets = [group['switches'] for group in inventory.get('groups') if group['name'] == options.group][0]

            if options.single:
                # inventory_targets.append(inventory['switches']options.single)
                single_device = [device for device in inventory.get('switches') if device['name'] == options.single][0]
                inventory_targets.append(single_device)

        except yaml.YAMLError as exc:
            print(exc)

    # Grab the play from the playbook file
    with open(options.playbook, 'r') as stream2:
        try:
            playbook = yaml.load(stream2)
            play = [play for play in playbook.get('plays') if play['name'] == options.play][0]
        except yaml.YAMLError as exc:
            print(exc)


    # Default timeout for pexpect.expect([]) calls
    timeout = 10

    pool = Pool(processes=4)
    func = partial(run_play, play)
    results = pool.map(func, inventory_targets)
    pool.close()
    pool.join()
