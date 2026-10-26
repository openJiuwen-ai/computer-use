namespace ComputerUse.Windows
{
    internal static class PrepareClickReceiverPass
    {
        internal const string Effects = "PREPARE";
        // Native authority verifies host policy and the selected mechanism's
        // preparation boundary. Public selection owns background-first order.
        internal static void Run(NativeWriteOperation operation, NativeInputContract contract, bool foreground, NativePointer pointer, Request r)
        {
            pointer.PrepareClick(operation, contract, foreground, r);
        }
    }
}
