# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Windows job census exercised with a substitute kernel."""

import ctypes
import time


U32, SIZE = ctypes.c_uint32, ctypes.c_size_t


JOB_CENSUS_ATTEMPTS = 4


class JobProcesses(ctypes.Structure):
    _fields_ = [("assigned", U32), ("returned", U32), ("pids", SIZE * 4096)]


class Sampler:
    def __init__(self, kernel, census=None, *, record_census=None):
        self.kernel = kernel
        self.census = census
        self.record_census = record_census

    def pids(self, job=None):
        if self.census is not None:
            return self.census()
        if job is None:
            raise ValueError("A local census requires the exact outer Job handle")
        for attempt in range(1, JOB_CENSUS_ATTEMPTS + 1):
            state = JobProcesses()
            success = self.kernel.QueryInformationJobObject(job, 3, ctypes.byref(state), ctypes.sizeof(state), None)
            winerror = None if success else ctypes.get_last_error()
            assigned, returned = state.assigned, state.returned
            if not success:
                status = "native_error"
            elif returned > assigned:
                status = "invalid_counts"
            elif assigned > len(state.pids):
                status = "capacity_exceeded"
            elif returned < assigned:
                status = "incomplete"
            else:
                pids = set(state.pids[:returned])
                status = "invalid_pids" if 0 in pids or len(pids) != returned else "complete"
            if self.record_census is not None and (attempt > 1 or status != "complete"):
                self.record_census(
                    {
                        "attempt": attempt,
                        "assigned": assigned,
                        "returned": returned,
                        "winerror": winerror,
                        "status": status,
                    }
                )
            if status == "complete":
                return pids
            if not success:
                raise ctypes.WinError(winerror)
            if status != "incomplete" or attempt == JOB_CENSUS_ATTEMPTS:
                raise RuntimeError(
                    f"Job process census {status} on attempt {attempt}: assigned={assigned}, returned={returned}"
                )
            # Discard the partial list and query again while Job membership can change.
            time.sleep(0.01)
