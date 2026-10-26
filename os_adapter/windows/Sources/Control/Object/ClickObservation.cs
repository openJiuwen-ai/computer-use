using System;
using System.Runtime.InteropServices;

namespace ComputerUse.Windows
{
    // QUERY: isolate only semantic metadata callbacks. Native identity, scope,
    // modality, cancellation and deadline checks run outside this catch.
    internal static class NativeClickObjectObservation
    {
        internal static void Read(NativeObjectState state, Action metadata, Action boundary, Request r)
        {
            r.Check();
            try
            {
                metadata();
            }
            catch (COMException error)
            {
                // UiaClient normally maps this to ElementNotAvailableException;
                // keep the lifetime failure fatal even for a raw COM provider.
                if ((uint)error.HResult == 0x80040201)
                    throw;
                r.Check();
                state.Role = null;
                state.Label = null;
                state.Enabled = null;
                state.Actions = new string[0];
                state.ActionsComplete = false;
                state.ClickMetadataUnavailable = "The UIA action metadata query failed; only independently validated input candidates remain available.";
            }

            r.Check();
            boundary();
            r.Check();
        }
    }
}
