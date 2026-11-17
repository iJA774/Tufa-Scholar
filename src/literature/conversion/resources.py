"""Per-parser memory ceiling, including Windows launches inside the start.ps1 job."""
import os

_job_handle = None


def limit_memory():
    ceiling = 768 * 1024 * 1024
    if os.name != 'nt':
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (ceiling, ceiling))
        return
    import ctypes
    from ctypes import wintypes

    class Basic(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_int64), ('PerJobUserTimeLimit', ctypes.c_int64),
                    ('LimitFlags', wintypes.DWORD), ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t), ('ActiveProcessLimit', wintypes.DWORD),
                    ('Affinity', ctypes.c_size_t), ('PriorityClass', wintypes.DWORD), ('SchedulingClass', wintypes.DWORD)]

    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_uint64) for name in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]

    class Extended(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', Basic), ('IoInfo', IO), ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t), ('PeakProcessMemoryUsed', ctypes.c_size_t), ('PeakJobMemoryUsed', ctypes.c_size_t)]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    global _job_handle
    handle = kernel.CreateJobObjectW(None, None)
    info = Extended()
    info.BasicLimitInformation.LimitFlags = 0x100  # JOB_OBJECT_LIMIT_PROCESS_MEMORY
    info.ProcessMemoryLimit = ceiling
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(info), ctypes.sizeof(info)) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        if handle: kernel.CloseHandle(handle)
        raise OSError('Cannot establish conversion memory limit')
    _job_handle = handle  # Keep the job alive until the parser process exits.
