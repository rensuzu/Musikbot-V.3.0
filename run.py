#!/usr/bin/env python3

from __future__ import print_function

import asyncio
import os
import ssl
import sys
import time
import logging
import tempfile
import traceback
import subprocess

from shutil import disk_usage, rmtree
from base64 import b64decode

try:
    import aiohttp
    import pathlib
    import importlib.util
except ImportError:
    pass


class GIT(object):
    @classmethod
    def works(cls):
        try:
            return bool(subprocess.check_output("git --version", shell=True))
        except Exception:
            return False

    @classmethod
    def run_upgrade_pull(cls):
        log.info("Attempting to upgrade with `git pull` on current path.")
        try:
            git_data = str(subprocess.check_output("git pull", shell=True))
            log.info(f"Result of git pull:  {git_data}")
        except Exception:
            log.exception("Upgrade failed, you need to run `git pull` manually.")


class PIP(object):
    @classmethod
    def run(cls, command, check_output=False):
        if not cls.works():
            raise RuntimeError("Could not import pip.")

        try:
            return PIP.run_python_m(*command.split(), check_output=check_output)
        except subprocess.CalledProcessError as e:
            return e.returncode
        except Exception:
            traceback.print_exc()
            print("Error using -m method")

    @classmethod
    def run_python_m(cls, *args, **kwargs):
        check_output = kwargs.pop("check_output", False)
        check = subprocess.check_output if check_output else subprocess.check_call
        return check([sys.executable, "-m", "pip"] + list(args))

    @classmethod
    def run_pip_main(cls, *args, **kwargs):
        import pip

        args = list(args)
        check_output = kwargs.pop("check_output", False)

        if check_output:
            from io import StringIO

            out = StringIO()
            sys.stdout = out

            try:
                pip.main(args)
            except Exception:
                traceback.print_exc()
            finally:
                sys.stdout = sys.__stdout__

                out.seek(0)
                pipdata = out.read()
                out.close()

                print(pipdata)
                return pipdata
        else:
            return pip.main(args)

    @classmethod
    def run_install(cls, cmd, quiet=False, check_output=False):
        return cls.run("install %s%s" % ("-q " if quiet else "", cmd), check_output)

    @classmethod
    def run_show(cls, cmd, check_output=False):
        return cls.run("show %s" % cmd, check_output)

    @classmethod
    def works(cls):
        try:
            import pip

            return True
        except ImportError:
            return False

    # noinspection PyTypeChecker
    @classmethod
    def get_module_version(cls, mod):
        try:
            out = cls.run_show(mod, check_output=True)

            if isinstance(out, bytes):
                out = out.decode()

            datas = out.replace("\r\n", "\n").split("\n")
            expectedversion = datas[3]

            if expectedversion.startswith("Version: "):
                return expectedversion.split()[1]
            else:
                return [x.split()[1] for x in datas if x.startswith("Version: ")][0]
        except Exception:
            pass

    @classmethod
    def get_requirements(cls, file="requirements.txt"):
        from pip.req import parse_requirements

        return list(parse_requirements(file))

    @classmethod
    def run_upgrade_requirements(cls):
        log.info(
            "Attempting to upgrade with `pip install --upgrade -r requirements.txt` on current path."
        )
        cmd = [sys.executable] + "-m pip install --upgrade -r requirements.txt".split()
        try:
            pip_data = subprocess.check_output(cmd)
            log.info(f"Result of pip upgrade:  {pip_data}")
        except Exception:
            log.exception(
                "Upgrade failed, you need to run `pip install --upgrade -r requirements.txt` manually."
            )


# Setup initial loggers

tmpfile = tempfile.TemporaryFile("w+", encoding="utf8")
log = logging.getLogger("launcher")
log.setLevel(logging.DEBUG)

sh = logging.StreamHandler(stream=sys.stdout)
sh.setFormatter(logging.Formatter(fmt="[%(levelname)s] %(name)s: %(message)s"))

sh.setLevel(logging.INFO)
log.addHandler(sh)

tfh = logging.StreamHandler(stream=tmpfile)
tfh.setFormatter(
    logging.Formatter(
        fmt="[%(relativeCreated).9f] %(asctime)s - %(levelname)s - %(name)s: %(message)s"
    )
)
tfh.setLevel(logging.DEBUG)
log.addHandler(tfh)


def finalize_logging():
    if os.path.isfile("logs/musicbot.log"):
        log.info("Moving old musicbot log")
        try:
            if os.path.isfile("logs/musicbot.log.last"):
                os.unlink("logs/musicbot.log.last")
            os.rename("logs/musicbot.log", "logs/musicbot.log.last")
        except Exception:
            pass

    with open("logs/musicbot.log", "w", encoding="utf8") as f:
        tmpfile.seek(0)
        f.write(tmpfile.read())
        tmpfile.close()

        f.write("\n")
        f.write(" PRE-RUN SANITY CHECKS PASSED ".center(80, "#"))
        f.write("\n\n")

    global tfh
    log.removeHandler(tfh)
    del tfh

    fh = logging.FileHandler("logs/musicbot.log", mode="a")
    fh.setFormatter(
        logging.Formatter(
            fmt="[%(relativeCreated).9f] %(name)s-%(levelname)s: %(message)s"
        )
    )
    fh.setLevel(logging.DEBUG)
    log.addHandler(fh)

    sh.setLevel(logging.INFO)

    dlog = logging.getLogger("discord")
    dlh = logging.StreamHandler(stream=sys.stdout)
    dlh.terminator = ""
    try:
        dlh.setFormatter(logging.Formatter("."))
    except ValueError:
        dlh.setFormatter(
            logging.Formatter(".", validate=False)
        )  # pylint: disable=unexpected-keyword-arg
    dlog.addHandler(dlh)


def bugger_off(msg="Press enter to continue . . .", code=1):
    input(msg)
    sys.exit(code)


# TODO: all of this
def sanity_checks(optional=True):
    log.info("Starting sanity checks")
    ## Required

    # Make sure we're on Python 3.8+
    req_ensure_py3()

    # Fix windows encoding fuckery
    req_ensure_encoding()

    # Make sure we're in a writeable env
    req_ensure_env()

    # Make our folders if needed
    req_ensure_folders()

    # For rewrite only
    req_check_deps()

    log.info("Required checks passed.")

    ## Optional
    if not optional:
        return

    # Check disk usage
    opt_check_disk_space()

    log.info("Optional checks passed.")


def req_ensure_py3():
    log.info("Checking for Python 3.8+")

    if sys.version_info < (3, 8):
        log.warning(
            "Python 3.8+ is required. This version is %s", sys.version.split()[0]
        )
        log.warning("Attempting to locate Python 3.8...")

        pycom = None

        if sys.platform.startswith("win"):
            log.info('Trying "py -3.8"')
            try:
                subprocess.check_output('py -3.8 -c "exit()"', shell=True)
                pycom = "py -3.8"
            except Exception:
                log.info('Trying "python3"')
                try:
                    subprocess.check_output('python3 -c "exit()"', shell=True)
                    pycom = "python3"
                except Exception:
                    pass

            if pycom:
                log.info("Python 3 found.  Launching bot...")
                os.system("start cmd /k %s run.py" % pycom)
                sys.exit(0)

        else:
            log.info('Trying "python3.8"')
            try:
                pycom = (
                    subprocess.check_output('python3.8 -c "exit()"'.split())
                    .strip()
                    .decode()
                )
            except Exception:
                pass

            if pycom:
                log.info(
                    "\nPython 3 found.  Re-launching bot using: %s run.py\n", pycom
                )
                os.execlp(pycom, pycom, "run.py")

        log.critical(
            "Could not find Python 3.8 or higher.  Please run the bot using Python 3.8"
        )
        bugger_off()


def req_check_deps():
    try:
        import discord

        if discord.version_info.major < 1:
            log.critical(
                "This version of MusicBot requires a newer version of discord.py. Your version is {0}. Try running update.py.".format(
                    discord.__version__
                )
            )
            bugger_off()
    except ImportError:
        # if we can't import discord.py, an error will be thrown later down the line anyway
        pass


def req_ensure_encoding():
    log.info("Checking console encoding")

    if (
        sys.platform.startswith("win")
        or sys.stdout.encoding.replace("-", "").lower() != "utf8"
    ):
        log.info("Setting console encoding to UTF-8")

        import io

        sys.stdout = io.TextIOWrapper(
            sys.stdout.detach(), encoding="utf8", line_buffering=True
        )
        # only slightly evil
        sys.__stdout__ = sh.stream = sys.stdout

        if os.environ.get("PYCHARM_HOSTED", None) not in (None, "0"):
            log.info("Enabling colors in pycharm pseudoconsole")
            sys.stdout.isatty = lambda: True


def req_ensure_env():
    log.info("Ensuring we're in the right environment")

    if os.environ.get("APP_ENV") != "docker" and not os.path.isdir(
        b64decode("LmdpdA==").decode("utf-8")
    ):
        log.critical(
            b64decode(
                "Qm90IHdhc24ndCBpbnN0YWxsZWQgdXNpbmcgR2l0LiBSZWluc3RhbGwgdXNpbmcgaHR0cDovL2JpdC5seS9tdXNpY2JvdGRvY3Mu"
            ).decode("utf-8")
        )
        bugger_off()

    try:
        assert os.path.isdir("config"), 'folder "config" not found'
        assert os.path.isdir("musicbot"), 'folder "musicbot" not found'
        assert os.path.isfile(
            "musicbot/__init__.py"
        ), "musicbot folder is not a Python module"

        assert importlib.util.find_spec("musicbot"), "musicbot module is not importable"
    except AssertionError as e:
        log.critical("Failed environment check, %s", e)
        bugger_off()

    try:
        os.mkdir("musicbot-test-folder")
    except Exception:
        log.critical("Current working directory does not seem to be writable")
        log.critical("Please move the bot to a folder that is writable")
        bugger_off()
    finally:
        rmtree("musicbot-test-folder", True)

    if sys.platform.startswith("win"):
        log.info("Adding local bins/ folder to path")
        os.environ["PATH"] += ";" + os.path.abspath("bin/")
        sys.path.append(os.path.abspath("bin/"))  # might as well


def req_ensure_folders():
    pathlib.Path("logs").mkdir(exist_ok=True)
    pathlib.Path("data").mkdir(exist_ok=True)


def opt_check_disk_space(warnlimit_mb=200):
    if disk_usage(".").free < warnlimit_mb * 1024 * 2:
        log.warning(
            "Less than %sMB of free space remains on this device" % warnlimit_mb
        )


#################################################


def respawn_bot_process(pybin=None):
    if not pybin:
        pybin = os.path.basename(sys.executable)
    exec_args = [pybin] + sys.argv

    sys.stdout.flush()
    sys.stderr.flush()
    logging.shutdown()

    if os.name == "nt":
        # On Windows, this creates a new process window that dies when the script exits.
        # Seemed like the best way to avoid a pile of processes While keeping clean output in the shell.
        # There is seemingly no way to get the same effect as os.exec* on unix here in windows land.
        # The moment we end this instance of the process, control is returned to the starting shell.
        subprocess.Popen(
            exec_args,
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        sys.exit(0)
    else:
        # On Unix/Linux/Mac this should immediately replace the current program.
        # No new PID, and the babies all get thrown out with the bath.  Kinda dangerous...
        # We need to make sure files and things are closed before we do this.
        os.execlp(exec_args[0], *exec_args)


async def main():
    # TODO: *actual* argparsing

    if "--no-checks" not in sys.argv:
        sanity_checks()

    finalize_logging()

    exit_signal = None
    tried_requirementstxt = False
    use_certifi = False
    tryagain = True

    loops = 0
    max_wait_time = 60

    while tryagain:
        # Maybe I need to try to import stuff first, then actually import stuff
        # It'd save me a lot of pain with all that awful exception type checking

        m = None
        try:
            from musicbot import MusicBot

            m = MusicBot()
            await m._doBotInit(use_certifi)
            await m.run()

        except (
            ssl.SSLCertVerificationError,
            aiohttp.client_exceptions.ClientConnectorCertificateError,
        ) as e:
            if isinstance(
                e, aiohttp.client_exceptions.ClientConnectorCertificateError
            ) and isinstance(e.__cause__, ssl.SSLCertVerificationError):
                e = e.__cause__
            else:
                log.critical(
                    "Certificate error is not a verification error, not trying certifi and exiting."
                )
                break

            # In case the local trust store does not have the cert locally, we can try certifi.
            # We don't want to patch working systems with a third-party trust chain outright.
            # These verify_code values come from OpenSSL:  https://www.openssl.org/docs/man1.0.2/man1/verify.html
            if e.verify_code == 20:  # X509_V_ERR_UNABLE_TO_GET_ISSUER_CERT_LOCALLY
                if use_certifi:
                    log.exception(
                        "Could not get Issuer Cert even with certifi.  Try: pip install --upgrade certifi "
                    )
                    break
                else:
                    log.warning(
                        "Could not get Issuer Certificate from default trust store, trying certifi instead."
                    )
                    use_certifi = True
                    continue

        except SyntaxError:
            log.exception("Syntax error (this is a bug, not your fault)")
            break

        except ImportError:
            # TODO: if error module is in pip or dpy requirements...

            if not tried_requirementstxt:
                tried_requirementstxt = True

                log.exception("Error starting bot")
                log.info("Attempting to install dependencies...")

                err = PIP.run_install("--upgrade -r requirements.txt")

                if err:  # TODO: add the specific error check back.
                    # The proper thing to do here is tell the user to fix their install, not help make it worse.
                    # Comprehensive return codes aren't really a feature of pip, we'd need to read the log, and so does the user.
                    print()
                    log.critical(
                        "This is not recommended! You can try to %s to install dependencies anyways."
                        % ["use sudo", "run as admin"][sys.platform.startswith("win")]
                    )
                    break
                else:
                    print()
                    log.info("Ok lets hope it worked")
                    print()
            else:
                log.exception("Unknown ImportError, exiting.")
                break

        except Exception as e:
            if hasattr(e, "__module__") and e.__module__ == "musicbot.exceptions":
                if e.__class__.__name__ == "HelpfulError":
                    log.info(e.message)
                    break

                elif e.__class__.__name__ == "TerminateSignal":
                    exit_signal = e
                    break

                elif e.__class__.__name__ == "RestartSignal":
                    if e.get_name() == "RESTART_SOFT":
                        loops = 0
                    else:
                        exit_signal = e
                        break
            else:
                log.exception("Error starting bot")

        finally:
            if m and (m.session or m.http.connector):
                # in case we never made it to m.run(), ensure cleanup.
                log.debug("Doing cleanup late.")
                await m._cleanup()

            if (not m or not m.init_ok) and not use_certifi:
                if any(sys.exc_info()):
                    # How to log this without redundant messages...
                    traceback.print_exc()
                break

            loops += 1

        sleeptime = min(loops * 2, max_wait_time)
        if sleeptime:
            log.info(f"Restarting in {sleeptime} seconds...")
            time.sleep(sleeptime)

    print()
    log.info("All done.")
    return exit_signal


if __name__ == "__main__":
    # py3.8 made ProactorEventLoop default on windows.
    # Now we need to make adjustments for a bug in aiohttp :)
    loop = asyncio.get_event_loop_policy().get_event_loop()
    exit_sig = loop.run_until_complete(main())
    if exit_sig:
        if exit_sig.__class__.__name__ == "RestartSignal":
            if exit_sig.get_name() == "RESTART_FULL":
                respawn_bot_process()
            elif exit_sig.get_name() == "RESTART_UPGRADE_ALL":
                PIP.run_upgrade_requirements()
                GIT.run_upgrade_pull()
                respawn_bot_process()
            elif exit_sig.get_name() == "RESTART_UPGRADE_PIP":
                PIP.run_upgrade_requirements()
                respawn_bot_process()
            elif exit_sig.get_name() == "RESTART_UPGRADE_GIT":
                GIT.run_upgrade_pull()
                respawn_bot_process()
        elif exit_sig.__class__.__name__ == "TerminateSignal":
            sys.exit(exit_sig.exit_code)
