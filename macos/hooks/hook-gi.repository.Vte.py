from PyInstaller.utils.hooks.gi import GiModuleInfo


def hook(hook_api):
    module = GiModuleInfo("Vte", "2.91", hook_api=hook_api)
    binaries, datas, hiddenimports = module.collect_typelib_data()
    hook_api.add_binaries(binaries)
    hook_api.add_datas(datas)
    hook_api.add_imports(*hiddenimports)
