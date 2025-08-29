from .base import CyclerPlugin, SniffResult

def get_builtin_plugins():
    from .biologic_mpt import BioLogicMPT
    return [BioLogicMPT]
