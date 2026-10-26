namespace ComputerUse.Windows
{
    internal static class RefreshClickPointPass
    {
        internal const string Effects = "QUERY";
        // Same retained element, current point. Capture mappings remain fixed.
        internal static void Run(NativeInputContract contract, NativePointer pointer, Request r)
        {
            pointer.RefreshClickPoint(contract, r);
        }
    }
}
