#!/usr/bin/env python3
"""victoria

Victoria is the SRE toolbelt -- a single command with multiple pluggable
subcommands for automating any number of 'toil' tasks that inhibit SRE
productivity.

Author:
    Sam Gibson <sgibson@glasswallsolutions.com>
"""

import argparse
import importlib
import importlib.util

import click

from .. import config
from .. import plugin
from ..util import basenamenoext

# Used for making it so we can use both -h and --help for help text.
CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}

# TODO(sam): Write full help text
HELP_TEXT = """
V.I.C.T.O.R.I.A.

\b
Very
Important
Commands for
Toil
Optimization:
Reducing
Inessential
Activities

Victoria is the SRE toolbelt -- a single command with multiple pluggable
subcommands for automating any number of 'toil' tasks that inhibit SRE
productivity.
"""

DEFAULT_CONFIG_FILENAME = "victoria.yaml"
"""The default filename of the config file. This will be loaded if no other
file is given."""

VERSION_NUMBER = "0.1"
"""The version number of the application, to print when calling with --version."""

APP_NAME = "victoria"
"""The name of the application."""


class VictoriaCLI(click.MultiCommand):
    """VictoriaCLI overrides click.MultiCommand to support loading click
    commands from python files. This is the bread and butter of the plugin
    system."""
    def __init__(self, name=None, **attrs):
        click.MultiCommand.__init__(self, name, **attrs)
        self.plugins = plugin.load_all()

    def list_commands(self, ctx):
        """List the available subcommands."""
        return [plgn.name for plgn in self.plugins]

    def get_command(self, ctx, name):
        """Get a subcommand from the list of installed plugins."""
        for plgn in self.plugins:
            # if the command name matches a loaded plugin name
            if plgn.name == name:
                # if the plugin has a config schema, load its config
                if plgn.config_schema and ctx.obj:
                    cfg = config.load_plugin_config(plgn, ctx.obj)
                    if not cfg:
                        # if there was an error loading the config, exit
                        raise SystemExit(1)
                    # HACK: patch the CLI context with the plugin config object
                    # this is a bit hacky, but it works
                    plgn.cli.context_settings = {"obj": cfg}
                return plgn.cli
        return None


@click.command(cls=VictoriaCLI,
               context_settings=CONTEXT_SETTINGS,
               help=HELP_TEXT)
@click.option(
    "-c",
    "--config-file",
    default=DEFAULT_CONFIG_FILENAME,
    metavar="FILE",
    help=f"The config file to load. Defaults to '{DEFAULT_CONFIG_FILENAME}'.")
@click.version_option(version=VERSION_NUMBER)
@click.pass_context
def cli(ctx, config_file):
    """This is the main CLI of the application. It uses VictoriaCLI to call
    loaded plugins based on subcommand name."""
    if config_file is None:
        raise SystemExit(1)


def main():
    # HACK: use argparse to get the config-file argument, as we need it in
    # VictoriaCLI methods before it would be parsed in the click cli() command...
    # this is utter filth, but I couldn't think of a better way to do it,
    # and it works transparent to the user
    parser = argparse.ArgumentParser(description="", add_help=False)
    parser.add_argument("-c", "--config-file", default=DEFAULT_CONFIG_FILENAME)
    parsed_args, _ = parser.parse_known_args()

    # load the config
    cfg = config.load(parsed_args.config_file)

    # execute the main CLI, passing the config in through the context
    cli.main(obj=cfg)


if __name__ == "__main__":
    main()