"""Construct named site capabilities for focused operation-boundary tests."""

from image_downloader.application.site_operation import SiteOperation, SiteOperationFactory
from image_downloader.plugins.plugin_invoker import PluginInvoker


def build_site(service, url, overrides=None, fallback_override=None, plugin_id=None, force_plugin=False):
    factory = SiteOperationFactory(service.config, service.registry, service.gateway, service.logger)
    record, plugin = factory.select(url, overrides, fallback_override, plugin_id, force_plugin)
    invoker = PluginInvoker(record.id, service.logger)
    context, gateway, policy = factory.build(url, record, plugin, invoker, overrides, None)
    return SiteOperation(record, plugin, context, gateway, policy, invoker)
