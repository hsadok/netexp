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
from typing import TextIO, Union, Optional, Callable


# from here: https://stackoverflow.com/a/287944/2027390
class bcolors:
    HEADER = "\033[95m"
    OKBLUE = "\033[94m"
    OKGREEN = "\033[92m"
    WARNING = "\033[93m"
    FAIL = "\033[91m"
    ENDC = "\033[0m"
    BOLD = "\033[1m"
    UNDERLINE = "\033[4m"


class LocalCommand:
    def __init__(
        self,
        command: str,
        pty: bool = False,
        dir: Optional[str] = None,
        source_bashrc: bool = False,
        print_command: Union[bool, TextIO] = False,
    ) -> None:
        if dir is not None:
            command = f"cd {dir}; {command}"

        if source_bashrc:
            command = f"source $HOME/.bashrc; {command}"

        self.proc_ = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=True,
        )

        if print_command is True:
            print_command = sys.stdout

        if print_command:
            print_command.write(f"command: {command}\n")

    def send(self, data: Union[str, bytes]) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.stdin.write(data)
        self.stdin.flush()

    def recv(self, size) -> str:
        original_proc_blocking = os.get_blocking(self.stdout.fileno())

        try:
            os.set_blocking(self.stdout.fileno(), False)
            data = self.stdout.read(size)
        finally:
            os.set_blocking(self.stdout.fileno(), original_proc_blocking)

        if data is None:
            return ""

        return data.decode("utf-8")

    def recv_stderr(self, size) -> str:
        original_proc_blocking = os.get_blocking(self.stderr.fileno())

        try:
            os.set_blocking(self.stderr.fileno(), False)
            data = self.stderr.read(size)
        finally:
            os.set_blocking(self.stderr.fileno(), original_proc_blocking)

        return data.decode("utf-8")

    def exit_status_ready(self) -> bool:
        return self.proc_.poll() is not None

    def watch(
        self,
        stop_condition: Optional[Callable[[], bool]] = None,
        keyboard_int: Optional[Callable[[], None]] = None,
        timeout: Optional[float] = None,
        stdout: Union[bool, TextIO] = False,
        stderr: Union[bool, TextIO] = False,
        stop_pattern: Optional[str] = None,
        max_match_length: Optional[int] = None,
    ) -> str:
        if stop_condition is None:
            stop_condition = self.exit_status_ready

        if timeout is None:
            deadline = None
        else:
            deadline = time.time() + timeout

        if stdout is True:
            stdout = sys.stdout

        if stderr is True:
            stderr = sys.stderr

        if max_match_length is None:
            max_match_length = 1024

        output = ""

        def continue_running():
            if (deadline is not None) and (time.time() > deadline):
                return False

            if stop_pattern is not None:
                search_len = min(len(output), max_match_length)
                if re.search(stop_pattern, output[-search_len:]):
                    return False

            return not stop_condition()

        keep_going = True

        original_proc_blocking = os.get_blocking(self.stdout.fileno())

        try:
            os.set_blocking(self.stdout.fileno(), False)

            while keep_going:
                s_out = select.select([self.stdout, self.stderr], [], [], 1)
                r, _, _ = s_out

                # We consume the output one more time after it's done.
                # This prevents us from missing the last bytes.
                keep_going = continue_running()

                if self.stdout is not None and self.stdout in r:
                    data = self.stdout.read(512)
                    data = data.decode("utf-8")
                    output += data
                    if stdout:
                        stdout.write(data)
                        stdout.flush()

                if self.stderr is not None and self.stderr in r:
                    data = self.stderr.read(512)
                    data = data.decode("utf-8")
                    output += data
                    if stderr:
                        stderr.write(data)
                        stderr.flush()

        except KeyboardInterrupt:
            if keyboard_int is not None:
                keyboard_int()
            raise

        finally:
            os.set_blocking(self.stdout.fileno(), original_proc_blocking)

        return output

    def recv_exit_status(self) -> int:
        self.proc_.communicate()
        return self.proc_.returncode

    def fileno(self) -> int:
        return self.stdout.fileno()

    def run_console_commands(
        self,
        commands: Union[str, list[str]],
        timeout: float = 1.0,
        console_pattern: Optional[str] = None,
        log_file: Union[bool, TextIO] = False,
    ) -> str:
        if not isinstance(commands, list):
            commands = [commands]

        if console_pattern is not None:
            console_pattern_len = len(console_pattern)
        else:
            console_pattern_len = None

        output = ""
        for cmd in commands:
            self.send(cmd + "\n")
            output += self.watch(
                keyboard_int=lambda: self.send("\x03"),
                timeout=timeout,
                stop_pattern=console_pattern,
                max_match_length=console_pattern_len,
                stdout=log_file,
                stderr=log_file,
            )

        return output

    def posix_shell(self) -> None:
        oldtty = termios.tcgetattr(sys.stdin)
        stdin_blocking = os.get_blocking(sys.stdin.fileno())
        try:
            tty.setcbreak(sys.stdin.fileno())
            os.set_blocking(sys.stdin.fileno(), False)

            self.send("\n")
            print("\n")

            while True:
                r, _, _ = select.select(
                    [self.stdout, self.stderr, sys.stdin], [], []
                )
                if self.stdout in r:
                    try:
                        data = self.recv(512)
                        if len(data) == 0:
                            break

                        sys.stdout.write(data)
                        sys.stdout.flush()

                    except socket.timeout:
                        pass

                if self.stderr in r:
                    try:
                        data = self.recv_stderr(512)
                        sys.stderr.write(data)
                        sys.stderr.flush()

                    except socket.timeout:
                        pass

                if sys.stdin in r:
                    x = sys.stdin.read(512)
                    if len(x) == 0:
                        break

                    if x.isprintable() or x in ["\r", "\n", "\t", "\x7f"]:
                        # If backspace, delete the last character
                        if x == "\x7f":
                            sys.stdout.write("\b \b")
                        else:
                            sys.stdout.write(x)
                        sys.stdout.flush()
                        self.send(x)

        finally:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)
            os.set_blocking(sys.stdin.fileno(), stdin_blocking)

    @property
    def stdin(self):
        assert self.proc_.stdin is not None
        return self.proc_.stdin

    @property
    def stdout(self):
        assert self.proc_.stdout is not None
        return self.proc_.stdout

    @property
    def stderr(self):
        assert self.proc_.stderr is not None
        return self.proc_.stderr


class RemoteCommand:
    def __init__(
        self, ssh_client: paramiko.SSHClient, *args, **kwargs
    ) -> None:
        self.ssh_client = ssh_client
        self.cmd_ = remote_command(ssh_client, *args, **kwargs)

    def send(self, data) -> None:
        self.cmd_.send(data)

    def recv(self, size) -> str:
        data = self.cmd_.recv(size)
        return data.decode("utf-8")

    def exit_status_ready(self) -> bool:
        return self.cmd_.exit_status_ready()

    def watch(self, *args, **kwargs) -> str:
        return watch_command(self.cmd_, *args, **kwargs)

    def recv_exit_status(self) -> int:
        return self.cmd_.recv_exit_status()

    def fileno(self) -> int:
        return self.cmd_.fileno()

    def run_console_commands(
        self,
        commands: Union[str, list[str]],
        timeout: float = 1.0,
        console_pattern: Optional[str] = None,
        log_file: Union[bool, TextIO] = False,
    ) -> str:
        if not isinstance(commands, list):
            commands = [commands]

        if console_pattern is not None:
            console_pattern_len = len(console_pattern)
        else:
            console_pattern_len = None

        output = ""
        for cmd in commands:
            self.send(cmd + "\n")
            output += self.watch(
                keyboard_int=lambda: self.send("\x03"),
                timeout=timeout,
                stop_pattern=console_pattern,
                max_match_length=console_pattern_len,
                stdout=log_file,
                stderr=log_file,
            )

        return output

    def posix_shell(self) -> None:
        posix_shell(self)

    def __del__(self):
        self.cmd_.close()


class LocalHost:
    def run_command(self, *args, **kwargs) -> LocalCommand:
        return LocalCommand(*args, **kwargs)


class RemoteHost:
    def __init__(
        self, host: str, nb_retries: int = 0, retry_interval: int = 1
    ) -> None:
        self.host = host
        self.nb_retries = nb_retries
        self.retry_interval = retry_interval

        self._ssh_client = None

    @property
    def ssh_client(self):
        if self._ssh_client is None:
            self._ssh_client = get_ssh_client(self.host)
        return self._ssh_client

    @ssh_client.deleter
    def ssh_client(self):
        if self._ssh_client is not None:
            self._ssh_client.close()
            del self._ssh_client
        self._ssh_client = None

    def __del__(self):
        del self.ssh_client

    def run_command(self, *args, **kwargs) -> RemoteCommand:
        return RemoteCommand(self.ssh_client, *args, **kwargs)


def get_host_from_hostname(hostname: str) -> Union[LocalHost, RemoteHost]:
    if hostname == "localhost":
        return LocalHost()
    else:
        return RemoteHost(hostname)


def remote_command(
    client: paramiko.SSHClient,
    command: str,
    pty: bool = False,
    dir: Optional[str] = None,
    source_bashrc: bool = False,
    print_command: Union[bool, TextIO] = False,
) -> paramiko.Channel:
    transport = client.get_transport()

    if transport is None:
        raise RuntimeError("Failed to get transport from client.")

    session = transport.open_session()

    if pty:
        session.setblocking(0)
        session.get_pty()

    if dir is not None:
        command = f"cd {dir}; {command}"

    if source_bashrc:
        command = f"source $HOME/.bashrc; {command}"

    session.exec_command(command)

    if print_command is True:
        print_command = sys.stdout

    if print_command:
        print_command.write(f"command: {command}\n")

    return session


def upload_file(host: str, local_path: str, remote_path: str):
    cp = subprocess.run(["scp", "-r", local_path, f"{host}:{remote_path}"])
    cp.check_returncode()


def download_file(host: str, remote_path: str, local_path: str):
    cp = subprocess.run(["scp", "-r", f"{host}:{remote_path}", local_path])
    cp.check_returncode()


def remove_remote_file(host: str, remote_path: str):
    cp = subprocess.run(["ssh", host, "rm", remote_path])
    cp.check_returncode()


def watch_command(
    command,
    stop_condition: Optional[Callable[[], bool]] = None,
    keyboard_int: Optional[Callable[[], None]] = None,
    timeout: Optional[float] = None,
    stdout: Union[bool, TextIO] = False,
    stderr: Union[bool, TextIO] = False,
    stop_pattern: Optional[str] = None,
    max_match_length: Optional[int] = None,
) -> str:
    if stop_condition is None:
        stop_condition = command.exit_status_ready

    assert stop_condition is not None

    if timeout is None:
        deadline = None
    else:
        deadline = time.time() + timeout

    if stdout is True:
        stdout = sys.stdout

    if stderr is True:
        stderr = sys.stderr

    if max_match_length is None:
        max_match_length = 1024

    output = ""

    def continue_running():
        if (deadline is not None) and (time.time() > deadline):
            return False

        if stop_pattern is not None:
            search_len = min(len(output), max_match_length)
            if re.search(stop_pattern, output[-search_len:]):
                return False

        return not stop_condition()

    keep_going = True
    try:
        while keep_going:
            time.sleep(0.01)

            # We consume the output one more time after it's done.
            # This prevents us from missing the last bytes.
            keep_going = continue_running()

            if command.recv_ready():
                data = command.recv(512)
                decoded_data = data.decode("utf-8")
                output += decoded_data
                if stdout:
                    stdout.write(decoded_data)
                    stdout.flush()

            if command.recv_stderr_ready():
                data = command.recv_stderr(512)
                decoded_data = data.decode("utf-8")
                output += decoded_data
                if stderr:
                    stderr.write(decoded_data)
                    stderr.flush()

    except KeyboardInterrupt:
        if keyboard_int is not None:
            keyboard_int()
        raise

    return output


def get_ssh_client(
    host, nb_retries: int = 0, retry_interval: float = 1
) -> paramiko.SSHClient:
    # adapted from https://gist.github.com/acdha/6064215
    client = paramiko.SSHClient()
    client._policy = paramiko.WarningPolicy()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    ssh_config = paramiko.SSHConfig()
    user_config_file = os.path.expanduser("~/.ssh/config")
    if os.path.exists(user_config_file):
        with open(user_config_file) as f:
            ssh_config.parse(f)

    cfg = {"hostname": host}

    user_config = ssh_config.lookup(host)

    for k in ("hostname", "username", "port"):
        if k in user_config:
            cfg[k] = user_config[k]

    if "user" in user_config:
        cfg["username"] = user_config["user"]

    if "proxycommand" in user_config:
        cfg["sock"] = paramiko.ProxyCommand(user_config["proxycommand"])

    if "identityfile" in user_config:
        cfg["pkey"] = paramiko.RSAKey.from_private_key_file(
            user_config["identityfile"][0]
        )

    trial = 0
    while True:
        trial += 1
        try:
            client.connect(**cfg)
            break
        except KeyboardInterrupt as e:
            raise e
        except Exception as e:
            time.sleep(retry_interval)
            if trial > nb_retries:
                raise e

    return client


def run_console_commands(
    console,
    commands: Union[str, list[str]],
    timeout: float = 1.0,
    console_pattern: Optional[str] = None,
    log_file: Union[bool, TextIO] = False,
):
    if not isinstance(commands, list):
        commands = [commands]

    if console_pattern is not None:
        console_pattern_len = len(console_pattern)
    else:
        console_pattern_len = None

    output = ""
    for cmd in commands:
        console.send(cmd + "\n")
        output += watch_command(
            console,
            keyboard_int=lambda: console.send("\x03"),
            timeout=timeout,
            stop_pattern=console_pattern,
            max_match_length=console_pattern_len,
            stdout=log_file,
            stderr=log_file,
        )

    return output


def posix_shell(chan):
    oldtty = termios.tcgetattr(sys.stdin)
    try:
        tty.setraw(sys.stdin.fileno())
        tty.setcbreak(sys.stdin.fileno())

        chan.send("\n")

        while True:
            r, _, _ = select.select([chan, sys.stdin], [], [])
            if chan in r:
                try:
                    data = chan.recv(512)
                    if len(data) == 0:
                        break
                    sys.stdout.write(data)
                    sys.stdout.flush()
                except socket.timeout:
                    pass
            if sys.stdin in r:
                x = sys.stdin.read(1)
                if len(x) == 0:
                    break
                # Make sure we read arrow keys.
                if x == "\x1b":
                    x += sys.stdin.read(2)
                chan.send(x)

    finally:
        termios.tcsetattr(sys.stdin, termios.TCSADRAIN, oldtty)


class IntelFpga:
    def __init__(
        self,
        fpga_id: str,
        run_console_cmd: str,
        load_bitstream_cmd: str,
        host_name: Optional[str] = None,
        load_bitstream: bool = True,
        log_file: Union[bool, TextIO] = False,
    ):
        if host_name is None:
            host_name = "localhost"

        self.host_name = host_name
        self.fpga_id = fpga_id
        self._host = None
        self.jtag_console = None
        self.run_console_cmd = run_console_cmd
        self.load_bitstream_cmd = load_bitstream_cmd
        self.log_file = log_file

        self.setup(load_bitstream)

    def run_jtag_commands(self, commands) -> str:
        if self.jtag_console is None:
            raise RuntimeError("JTAG console not started")

        return self.jtag_console.run_console_commands(
            commands, console_pattern="\r\n% ", log_file=self.log_file
        )

    def launch_console(self, max_retries=5):
        retries = 0
        cmd = Path(self.run_console_cmd)
        cmd_path = cmd.parent
        cmd = f"./{cmd.name} {self.fpga_id}"

        while True:
            app = self.host.run_command(
                cmd, pty=True, dir=cmd_path, source_bashrc=True
            )
            app.watch(
                keyboard_int=lambda: app.send("\x03"),
                timeout=10,
                stdout=self.log_file,
                stderr=self.log_file,
            )

            app.send("source path.tcl\n")
            output = app.watch(
                keyboard_int=lambda: app.send("\x03"),
                timeout=2,
                stdout=self.log_file,
                stderr=self.log_file,
            )

            lines = output.split("\n")
            lines = [
                ln
                for ln in lines
                if f"@1#{self.fpga_id}#Intel " in ln and ": " in ln
            ]

            if len(lines) == 1:
                break

            app.send("\x03")

            retries += 1
            if retries >= max_retries:
                raise RuntimeError(
                    f"Failed to determine device {retries} times"
                )

            time.sleep(1)

        device = lines[0].split(":")[0]

        self.jtag_console = app
        self.run_jtag_commands(f"set_jtag {device}")

    def setup(self, load_bitstream):
        retries = 0
        cmd = Path(self.load_bitstream_cmd)
        cmd_path = cmd.parent
        cmd = f"./{cmd.name} {self.fpga_id}"

        while load_bitstream:
            app = self.host.run_command(
                cmd, pty=True, dir=cmd_path, source_bashrc=True
            )
            output = app.watch(
                keyboard_int=lambda: app.send("\x03"),
                stdout=self.log_file,
                stderr=self.log_file,
            )

            status = app.recv_exit_status()
            if status == 0:
                break

            if "Synchronization failed" in output:
                raise RuntimeError(
                    "Synchronization failed, try power cycling the host"
                )

            warnings.warn("Failed to load bitstream, retrying.")

            retries += 1
            if retries >= 5:
                raise RuntimeError(f"Failed to load bitstream {retries} times")

        self.launch_console()

    def interactive_shell(self):
        assert self.jtag_console is not None
        self.jtag_console.posix_shell()

    @property
    def host(self):
        if self._host is None:
            self._host = get_host_from_hostname(self.host_name)
        return self._host

    @host.deleter
    def host(self):
        if self._host is None:
            return
        del self._host
        self._host = None

    def __del__(self):
        if self.jtag_console is not None:
            del self.jtag_console
            self.jtag_console = None
        del self.host


def get_host_available_frequencies(
    host: Union[LocalHost, RemoteHost],
    core: int,
    stdout: Union[bool, TextIO] = False,
    stderr: Union[bool, TextIO] = False,
) -> list[int]:
    cmd = host.run_command(
        f"sudo cat /sys/devices/system/cpu/cpu{core}/cpufreq/"
        f"scaling_available_frequencies",
        pty=True,
    )
    out = cmd.watch(stdout=stdout, stderr=stderr)
    status = cmd.recv_exit_status()
    if status != 0:
        raise RuntimeError("Could not probe available frequencies")

    print(out)
    frequencies = []
    for f in out.split(" "):
        try:
            f = int(f.strip())
            frequencies.append(f)
        except ValueError:
            pass

    return frequencies


def set_host_clock(
    host: Union[LocalHost, RemoteHost], clock: int, cores: list[int]
) -> None:
    """Set clock frequency for a remote host.

    Args:
        host: Host to set the clock frequency for.
        clock: CPU frequency to be set (in kHz). If `0` set frequency to
          maximum supported by the core.
        cores: List of cores to set the frequency to.
    """
    # Arch has good docs about this:
    #   https://wiki.archlinux.org/title/CPU_frequency_scaling
    def raw_set_core_freq(freq_type: str, core: int, freq: int):
        cmd = host.run_command(
            f"echo {freq} | sudo tee /sys/devices/system/cpu/cpu{core}/"
            f"cpufreq/scaling_{freq_type}_freq",
            pty=True,
        )
        cmd.watch(stdout=False, stderr=False)
        status = cmd.recv_exit_status()

        if status != 0:
            raise RuntimeError(f"Could not set {freq_type} frequency")

    for core in cores:
        available_freqs = get_host_available_frequencies(host, core)

        if clock == 0:
            clock = available_freqs[0]  # Set clock to maximum.

        if clock not in available_freqs:
            raise RuntimeError(f'Clock "{clock}" not supported by CPU.')

        cmd = host.run_command(
            f"sudo cat /sys/devices/system/cpu/cpu{core}/cpufreq/"
            f"cpuinfo_cur_freq",
            pty=True,
        )
        out = cmd.watch(stdout=False, stderr=False)
        status = cmd.recv_exit_status()

        if status != 0:
            raise RuntimeError("Could not retrieve current frequency")

        cur_freq = int(out)

        if clock == cur_freq:
            continue

        if clock < cur_freq:
            raw_set_core_freq("min", core, clock)
            raw_set_core_freq("max", core, clock)
        else:
            raw_set_core_freq("max", core, clock)
            raw_set_core_freq("min", core, clock)
