// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    internal static class NativeObjectChecks
    {
        internal static NativeObjectState ReadForContract(ObjectDispatchPlan plan, Request r,
                                                          INativeObjectProvider provider)
        {
            if (!plan.Contract.RequireCollapsedExpansion)
            {
                var ordinary = provider.Read(plan.Target, plan.Contract.Within, r);
                if (plan.Contract.ClickTargetCondition != null)
                    plan.Contract.ClickTargetCondition.Require(r);
                return ordinary;
            }
            if (plan.Unit != "native_action" || plan.Contract.Action != "ExpandCollapse.Expand")
                throw new Failure("invalid_request",
                                  "the collapsed expansion condition belongs only to its selected Expand action");
            var expansion = provider as INativeExpansionObjectProvider;
            if (expansion == null)
                throw new Failure("input_unavailable", "the selected Expand condition cannot currently be confirmed");
            var state = expansion.ReadExpansion(plan.Target, plan.Contract.Within, r);
            if (plan.Contract.ClickTargetCondition != null)
                plan.Contract.ClickTargetCondition.Require(r);
            return state;
        }
        internal static void CheckState(NativeObjectState state, NativeInputContract contract)
        {
            if (contract.FixedClickChoice && contract.Unit == "native_action" && contract.Action == "Invoke.Invoke" &&
                state.Actions.Contains("ExpandCollapse.Expand"))
                throw new Failure(
                    "input_unavailable",
                    "the selected Invoke control acquired an Expand action; no requested action was sent");
            if (contract.RequireCollapsedExpansion &&
                (contract.Unit != "native_action" || contract.Action != "ExpandCollapse.Expand" ||
                 state.CollapsedExpansion != true))
                throw new Failure(
                    "input_unavailable",
                    "the selected Expand control is no longer confirmed collapsed; no requested action was sent");
            if (state.Enabled != true || state.Role != contract.Role)
                throw new Failure("input_unavailable", "the selected control's enabled state or role changed");
            if (contract.Unit == "native_action" &&
                    (!state.ActionsComplete || state.Actions.Count(x => x == contract.Action) != 1) ||
                contract.Unit == "native_text_edit" && state.Replaceable != true)
                throw new Failure("input_unavailable", "the selected native capability is no longer available");
        }
    }
}
