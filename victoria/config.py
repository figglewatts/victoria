"""config

Contains various classes and methods for loading the config of Victoria.

Author:
    Sam Gibson <sgibson@glasswallsolutions.com>
"""
import io
import logging.config
import os
from os import path
from typing import List, Mapping

import appdirs
import click
from marshmallow import Schema, fields, post_load, ValidationError, EXCLUDE
import pkg_resources
import yaml

from .plugin import Plugin
from . import storage
from . import encryption

APP_NAME = "victoria"
"""What the app is called."""

APP_AUTHOR = "GlasswallSRE"
"""Who the author of the application is."""

DEFAULT_CONFIG_NAME = "victoria.yaml"
"""The default filename of the config file. This will be loaded if no other
file is given."""

EXAMPLE_CONFIG_FILE = "victoria_example.yaml"
"""The example config that will be created if a config wasn't found."""


def get_config_loc() -> str:
    """Get the path to the config file."""
    return path.join(appdirs.user_config_dir(APP_NAME, APP_AUTHOR),
                     DEFAULT_CONFIG_NAME)


def ensure() -> None:
    """Ensure that a config exists in the location."""
    if not path.exists(get_config_loc()):
        print("Didn't find config file -- installing default to "
              f"{get_config_loc()}")

        # make all of the directories to the config file
        try:
            os.makedirs(path.dirname(get_config_loc()))
        except FileExistsError:
            pass

        # now write the example config file to the config location
        with open(get_config_loc(), "w") as cfg_file:
            # load the example config from package resources and replace any
            # funky windows line endings that may be in it
            example_cfg_file = pkg_resources.resource_string(
                "victoria",
                EXAMPLE_CONFIG_FILE).decode("utf-8").replace("\r", "")
            cfg_file.write(example_cfg_file)


class ConfigSchema(Schema):
    """Marshmallow schema for the Config object."""
    logging_config = fields.Dict(required=True)
    storage_providers = fields.Dict(required=False)
    encryption_provider = fields.Nested(
        encryption.EncryptionProviderConfigSchema, required=False)
    plugins_config_location = fields.Mapping(keys=fields.Str(),
                                             values=fields.Str(),
                                             default={})
    plugins_config = fields.Dict(required=False)

    @post_load
    def make_config_obj(self, data, **kwargs):
        return Config(**data)


CONFIG_SCHEMA = ConfigSchema(unknown=EXCLUDE)
"""Instance of ConfigSchema used for validating loaded configs."""


class Config:
    """Config is used for storing deserialized values from Config files.

    Attributes:
        logging_config (dict): The config to use for logging.
        plugins_config (dict): The config to use for plugins.
        plugins_config_location (Mapping[str, str]): Config file location overrides for plugins.
        storage_providers (dict): Data used for connecting to storage.
        encryption_provider (EncryptionProviderConfig): Config for the encryption provider.
    """
    def __init__(
        self,
        logging_config: dict,
        plugins_config: dict = None,
        plugins_config_location: Mapping[str, str] = {},
        storage_providers: dict = None,
        encryption_provider: encryption.EncryptionProviderConfig = None):
        self.logging_config = logging_config
        logging.config.dictConfig(logging_config)
        self.plugins_config = plugins_config
        self.plugins_config_location = plugins_config_location
        self.storage_providers = storage_providers
        self.encryption_provider = encryption_provider

    def __eq__(self, other):
        if isinstance(self, other.__class__):
            return self.logging_config == other.logging_config \
                and self.plugins_config == other.plugins_config \
                and self.plugins_config_location == other.plugins_config_location \
                and self.storage_providers == other.storage_providers \
                and self.encryption_provider == other.encryption_provider
        return False

    def get_storage(self, provider: str) -> storage.StorageProvider:
        if provider not in self.storage_providers:
            logging.error(
                f"no configuration for storage provider '{provider}'")
            return None
        return storage.make_provider(provider,
                                     **self.storage_providers[provider])

    def get_encryption(self) -> encryption.EncryptionProvider:
        if self.encryption_provider.provider not in encryption.PROVIDERS_MAP.keys(
        ):
            logging.error(
                f"encryption provider '{self.encryption_provider.provider}' not valid"
            )
            return None
        return encryption.make_provider(self.encryption_provider.provider,
                                        **self.encryption_provider.config)


pass_config = click.make_pass_decorator(Config)
"""Decorator for passing the Victoria config to a command."""


def _print_validation_err(err: ValidationError,
                          name: str,
                          use_log: bool = True) -> None:
    """Internal function used for printing a validation error in the Schema.

    Args:
        err (ValidationError): The error to log.
        name (str): A human-readable identifier for the Schema data source. 
            Like a filename.
        use_log (bool): Whether to use the logging package to log the error.
            This is needed because sometimes a validation error might occur
            when loading the logging config, before it's configured.
    """
    # build up a string for each error
    log_str = []
    log_str.append(f"Error validating config '{name}':")
    for field_name, err_msgs in err.messages.items():
        log_str.append(f"{field_name}: {err_msgs}")

    # print the joined up string
    print(" ".join(log_str))


def load_plugin_config(plugin: Plugin, cfg: Config) -> object:
    """Load the config of a plugin from the main Victoria config.

    Args:
        plugin (Plugin): The plugin to load the config for.
        cfg (Config): The config file to load the config from.

    Returns:
        object: The loaded config object. It will be the same type as whatever
            the plugin's config marshmallow schema will marshal it to, or None
            if there was some error loading the plugin config.

    Raises:
        ValueError: if the plugin didn't have a config schema.
    """
    # we don't want to try to load a config from the config if it wasn't loaded
    if cfg is None:
        logging.warning(f"Can't load '{plugin.name}' config: victoria config "
                        "not loaded.")
        return None

    if plugin.config_schema is None:
        raise ValueError(f"Can't load plugin config: plugin '{plugin.name}' "
                         "did not have config schema")

    raw_config = {}

    # check to see if there's a location override for the config
    if plugin.name in cfg.plugins_config_location:
        loc = cfg.plugins_config_location[plugin.name]
        raw_config = _handle_config_file_override(loc, plugin, cfg)
    elif plugin.name in cfg.plugins_config:
        # otherwise use the one in the main config
        if not cfg.plugins_config:
            logging.error("Can't load plugin config: config did not "
                          "have 'plugins_config' section")
            return None
        raw_config = cfg.plugins_config[plugin.name]
    else:
        logging.error(
            "Can't load plugin config: "
            f"config did not have section for plugin '{plugin.name}' "
            "and no location was given in 'plugins_config_location'")
        return None

    # try validating the contents of the plugin section with the schema
    try:
        loaded_config = plugin.config_schema.load(raw_config)
        return loaded_config
    except ValidationError as err:
        # if the loaded YAML wasn't a valid config
        _print_validation_err(err, f"plugins_config.{plugin.name}")
        return None


def _handle_config_file_override(override_loc: str, plugin: Plugin,
                                 cfg: Config) -> object:
    provider_type, path = tuple(override_loc.split("://"))
    provider = cfg.get_storage(provider_type)
    config_file = io.BytesIO()
    provider.retrieve(path, config_file)
    config_str = config_file.getvalue().decode("utf-8")
    return yaml.safe_load(config_str)


def load(config_path: str) -> Config:
    """Load a config file from a given path and make sure it's valid.

    If an error occurred, it will print it and return None.

    Args:
        config_path (str): The path to the YAML config file.

    Returns:
        Config: The loaded config file, or None if an error occurred.
    """
    try:
        with open(config_path, "r") as config_file:
            raw_config = yaml.safe_load(config_file)
            loaded_config = CONFIG_SCHEMA.load(raw_config)
            return loaded_config
    except OSError as err:
        # if there was an error opening the file
        print("Error opening config file: " + str(err))
        return None
    except yaml.YAMLError as err:
        # if the loaded YAML was invalid YAML
        print("Error in config file: " + str(err))
        return None
    except ValidationError as err:
        # if the loaded YAML wasn't a valid config
        _print_validation_err(err, config_path)
        return None
    except (ValueError, TypeError, AttributeError, ImportError) as err:
        # if the logging config was invalid
        print("Unable to load logging config: " + str(err))
        return None
