
import os
import re
import select
import socket
import subprocess
import sys
import termios
import time
import tty
import warnings

import paramiko

from pathlib import Path

LOAD_BITSTREAM_CMD = 'hardware_test/load_bitstream.sh'
RUN_CONSOLE_CMD = 'hardware_test/run_console.sh'


# from here: https://stackoverflow.com/a/287944/2027390
class bcolors:
    HEADER = '\033[95m'
    OKBLUE = '\033[94m'
    OKGREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'


def remote_command(client, command, pty=False, dir=None, source_bashrc=False,
                   print_command=False):
    transport = client.get_transport()
    session = transport.open_session()

    if pty:
        session.setblocking(0)
        session.get_pty()

    if dir is not None:
        command = f'cd {dir}; {command}'

    if source_bashrc:
        command = f'source $HOME/.bashrc; {command}'

    session.exec_command(command)

    if print_command:
        print(f'command: {command}')

    return session


def upload_file(host, local_path, remote_path):
    subprocess.run(['scp', '-r', local_path, f'{host}:{remote_path}'])


def download_file(host, remote_path, local_path):
    subprocess.run(['scp', '-r', f'{host}:{remote_path}', local_path])


def remove_remote_file(host, remote_path):
    subprocess.run(['ssh', host, 'rm', remote_path])


def watch_command(command, stop_condition=None, keyboard_int=None,
                  timeout=None, stdout=True, stderr=True, stop_pattern=None,
                  max_match_length=1024):
    if stop_condition is None:
        stop_condition = command.exit_status_ready

    if timeout is not None:
        deadline = time.time() + timeout

    output = ''

    def continue_running():
        if (stop_pattern is not None):
            search_len = min(len(output), max_match_length)
            if re.search(stop_pattern, output[-search_len:]):
                return False
        return not stop_condition()

    stop = False
    try:
        while not stop:
            time.sleep(0.01)

            # We consume the output one more time after it's done.
            # This prevents us from missing the last bytes.
            if (timeout is not None) and (time.time() > deadline):
                stop = True
            elif not continue_running():
                stop = True

            if command.recv_ready():
                data = command.recv(512)
                decoded_data = data.decode('utf-8')
                output += decoded_data
                if stdout:
                    sys.stdout.write(decoded_data)
                    sys.stdout.flush()

            if command.recv_stderr_ready():
                data = command.recv_stderr(512)
                decoded_data = data.decode('utf-8')
                output += decoded_data
                if stderr:
                    sys.stderr.write(decoded_data)
                    sys.stderr.flush()

    except KeyboardInterrupt:
        if keyboard_int is not None:
            keyboard_int()
        raise

    return output


def get_ssh_client(host, nb_retries=0, retry_interval=1):
    # adapted from https://gist.github.com/acdha/6064215
    client = paramiko.SSHClient()
    client._policy = paramiko.WarningPolicy()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh_config = paramiko.SSHConfig()
    user_config_file = os.path.expanduser("~/.ssh/config")
    if os.path.exists(user_config_file):
        with open(user_config_file) as f:
            ssh_config.parse(f)

    cfg = {'hostname': host}

    user_config = ssh_config.lookup(host)

    for k in ('hostname', 'username', 'port'):
        if k in user_config:
            cfg[k] = user_config[k]

    if 'user' in user_config:
        cfg['username'] = user_config['user']

    if 'proxycommand' in user_config:
        cfg['sock'] = paramiko.ProxyCommand(user_config['proxycommand'])

    if 'identityfile' in user_config:
        cfg['pkey'] = paramiko.RSAKey.from_private_key_file(
                        user_config['identityfile'][0])

    trial = 0
    while True:
        if trial > nb_retries:
            raise paramiko.ssh_exception.NoValidConnectionsError
        trial += 1
        try:
            client.connect(**cfg)
            break
        except KeyboardInterrupt as e:
            raise e
        except:
            time.sleep(retry_interval)
            continue

    return client


def run_console_commands(console, commands, timeout=1, console_pattern=None,
                         verbose=False):
    if not isinstance(commands, list):
        commands = [commands]

    if console_pattern is not None:
        console_pattern_len = len(console_pattern)
    else:
        console_pattern_len = None

    output = ''
    for cmd in commands:
        console.send(cmd + '\n')
        output += watch_command(console,
                                keyboard_int=lambda: console.send('\x03'),
                                timeout=timeout, stop_pattern=console_pattern,
                                max_match_length=console_pattern_len,
                                stdout=verbose, stderr=verbose)

    return output


def posix_shell(chan):
    oldtty = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())
        chan.settimeout(0.0)

        chan.send('\n')

        while True:
            r, _, _ = select.select([chan, sys.stdin], [], [])
            if chan in r:
                try:
                    data = chan.recv(512)
                    decoded_data = data.decode('utf-8')
                    if len(decoded_data) == 0:
                        break
                    sys.stdout.write(decoded_data)
                    sys.stdout.flush()
                except socket.timeout:
                    pass
            if sys.stdin in r:
                x = sys.stdin.read(1)
                if len(x) == 0:
                    break
                # Make sure we read arrow keys.
                if x == '\x1b':
                    x += sys.stdin.read(2)
                chan.send(x)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)


class RemoteIntelFpga:
    def __init__(self, host: str, fpga_id: str, remote_dir: str,
                 load_bitstream: bool = True, verbose=False):
        self.host = host
        self.fpga_id = fpga_id
        self._ssh_client = None
        self.jtag_console = None
        self.remote_dir = remote_dir
        self.verbose = verbose

        self.setup(load_bitstream)

    def run_jtag_commands(self, commands):
        return run_console_commands(self.jtag_console, commands,
                                    console_pattern='\r\n% ',
                                    verbose=self.verbose)

    def launch_console(self, max_retries=5):
        retries = 0
        cmd = Path(RUN_CONSOLE_CMD)
        cmd_path = Path(self.remote_dir) / cmd.parent
        cmd = f'./{cmd.name} {self.fpga_id}'

        while True:
            app = remote_command(self.ssh_client, cmd, pty=True, dir=cmd_path,
                                 source_bashrc=True)
            watch_command(app, keyboard_int=lambda: app.send('\x03'),
                          timeout=10, stdout=self.verbose, stderr=self.verbose)

            app.send('source path.tcl\n')
            output = watch_command(app, keyboard_int=lambda: app.send('\x03'),
                                   timeout=2, stdout=self.verbose,
                                   stderr=self.verbose)
            lines = output.split('\n')
            lines = [
                ln for ln in lines
                if f'@1#{self.fpga_id}#Intel ' in ln and ': ' in ln
            ]

            if len(lines) == 1:
                break

            app.send('\x03')

            retries += 1
            if retries >= max_retries:
                raise RuntimeError(
                    f'Failed to determine device {retries} times')

            time.sleep(1)

        device = lines[0].split(':')[0]

        self.jtag_console = app
        self.run_jtag_commands(f'set_jtag {device}')

    def setup(self, load_bitstream):
        retries = 0
        cmd = Path(LOAD_BITSTREAM_CMD)
        cmd_path = Path(self.remote_dir) / cmd.parent
        cmd = f'./{cmd.name} {self.fpga_id}'

        while load_bitstream:
            app = remote_command(self.ssh_client, cmd, pty=True, dir=cmd_path,
                                 source_bashrc=True)
            output = watch_command(app, keyboard_int=lambda: app.send('\x03'),
                                   stdout=self.verbose, stderr=self.verbose)
            status = app.recv_exit_status()
            if status == 0:
                break

            if 'Synchronization failed' in output:
                raise RuntimeError('Synchronization failed, try power cycling '
                                   'the host')

            warnings.warn('Failed to load bitstream, retrying.')

            retries += 1
            if retries >= 5:
                raise RuntimeError(f'Failed to load bitstream {retries} times')

        self.launch_console()

    def interactive_shell(self):
        posix_shell(self.jtag_console)

    @property
    def ssh_client(self):
        if self._ssh_client is None:
            self._ssh_client = get_ssh_client(self.host)
        return self._ssh_client

    @ssh_client.deleter
    def ssh_client(self):
        self._ssh_client.close()
        del self._ssh_client
        self._ssh_client = None

    def __del__(self):
        if self.jtag_console is not None:
            self.jtag_console.close()
        del self.ssh_client
