using System.Collections.Generic;

namespace ComputerUse.Windows
{
    internal sealed class NativeClickPreparationAuthority
    {
        readonly string family;
        readonly Dictionary<string, object> original, target;
        readonly bool hostAllowsForeground, hasNoBackgroundPreparation;
        internal NativeClickPreparationAuthority(string family, NativeWriteOperation operation, Dictionary<string, object> target, bool hostAllowsForeground, bool hasNoBackgroundPreparation)
        {
            this.family = family;
            original = new Dictionary<string, object>(operation.Original);
            this.target = new Dictionary<string, object>(target);
            this.hostAllowsForeground = hostAllowsForeground;
            this.hasNoBackgroundPreparation = hasNoBackgroundPreparation;
        }

        internal void Require(NativeWriteOperation operation, NativeInputContract contract)
        {
            if (!hostAllowsForeground)
                throw new Failure("mode_unsupported", "host policy does not permit foreground assistance");
            if (!hasNoBackgroundPreparation || contract.Click == null || contract.Click.RouteFamily != family || !NativeWriteOperation.Same(original, operation.Original) || !NativeWriteOperation.Same(target, contract.Target) || operation.Selected == null || !NativeWriteOperation.Same(target, operation.Selected))
                throw new Failure("stale_target", "foreground preparation authority does not belong to the original click scope");
            if (operation.InputTerminated)
                throw new Failure("execution_unknown", "input or preparation already prevents another request");
        // These native facts do not claim that other background candidates
        // were exhausted. That decision belongs to the common Select/Gate.
        }
    }
}
