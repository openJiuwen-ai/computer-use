using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal static class ResolveClickContractPass
    {
        internal const string Effects = "QUERY";
        // Resolve only the selected opaque contract. Never choose an alternate
        // native action or transport, including after a preparation failure.
        internal static NativeInputContract Run(NativeWriteOperation operation, Dictionary<string, object> command)
        {
            NativeInputContract contract;
            if (!operation.ClickV1 || (string)operation.Intent["kind"] != "click")
                throw new Failure("invalid_request", "the click contract was not enabled for this operation");
            if (operation.InputTerminated)
                throw new Failure("execution_unknown", "the operation cannot dispatch additional input");
            if (!operation.Contracts.TryGetValue((string)command["primitiveId"], out contract) || !NativeWriteOperation.Same(Wire.Object(command["target"]), contract.Target))
                throw new Failure("stale_target", "the selected click contract expired or belongs to another target");
            if ((string)command["kind"] != contract.Unit || contract.Unit != "native_action" && contract.Unit != "pointer_sequence")
                throw new Failure("invalid_request", "v1 click executes its selected unit without a separate prepare command");
            if (contract.Click == null)
                throw new Failure("invalid_request", "the selected primitive has no negotiated click contract");
            return contract;
        }
    }
}
