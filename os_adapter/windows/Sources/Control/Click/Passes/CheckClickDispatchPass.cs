namespace ComputerUse.Windows
{
    internal static class CheckClickDispatchPass
    {
        internal const string Effects = "QUERY";
        // Last HWND/point/capture/idle check and dispatch remain in one call.
        internal static void Run(NativeInputContract contract, NativePointer pointer, Request r)
        {
            pointer.CheckClick(contract, r);
        }
    }
}
