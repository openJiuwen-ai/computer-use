// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;

namespace ComputerUse.Windows
{
    // Passive candidates supplement the selected window's observations. The
    // explicit query/check/retention order performs no input and never retries.
    internal sealed class NativeReadDiscoveryPipeline
    {
        readonly INativeReadDiscoveryProvider provider;
        internal NativeReadDiscoveryPipeline(INativeReadDiscoveryProvider provider)
        {
            this.provider = provider;
        }
        internal NativeReadDiscoveryResult Run(string source, Request request, string[] properties = null)
        {
            var relations = new List<Dictionary<string, object>>();
            foreach (var kind in new[] { NativeReadDiscoveryKind.Focus, NativeReadDiscoveryKind.Point })
            {
                string property = kind == NativeReadDiscoveryKind.Focus ? "UIA.FocusedElement" : "UIA.PointElement";
                if (properties != null && !properties.Contains(property))
                    continue;
                string state = "absent", detail = null, node = null;
                try
                {
                    var gate = CaptureDiscoveryScopePass.Run(kind, request, provider);
                    if (!gate.Eligible)
                    {
                        state = gate.State;
                        detail = gate.Detail;
                    }
                    else
                    {
                        var candidate = QueryDiscoveryCandidatePass.Run(kind, gate.Stamp, request, provider);
                        if (!ValidateDiscoveryStabilityPass.Run(kind, gate.Stamp, request, provider))
                        {
                            state = "error";
                            detail = "Native discovery scope changed; the candidate was discarded.";
                        }
                        else if (candidate == null)
                        {
                            detail = "UIA reported no element for this passive discovery source.";
                        }
                        else
                        {
                            var retained =
                                RetainDiscoveryCandidatePass.Run(kind, gate.Stamp, candidate, request, provider);
                            node = retained.Node;
                            state = retained.State;
                            detail = retained.Detail;
                        }
                    }
                }
                catch (Exception error)
                {
                    var failure = error as Failure;
                    if (failure != null && (failure.Code == "cancelled" || failure.Code == "deadline_exceeded" ||
                                            failure.Code == "stale_target"))
                        throw;
                    request.Check();
                    var unavailable = error as NativeFieldUnavailable;
                    bool timeout = error is TimeoutException || (uint)error.HResult == 0x80131505 ||
                                   (uint)error.HResult == 0x8001011F;
                    state = unavailable == null ? timeout ? "timeout" : "error" : unavailable.State;
                    detail = NativeReadStore.Text("Passive UIA discovery was unavailable; no probe was retried: " +
                                                  error.Message);
                }
                relations.Add(Wire.Obj(
                    "source", source, "property", property, "targets", node == null ? new string[0] : new[] { node },
                    "certainty", node == null ? "unknown" : "confirmed", "state", state, "detail",
                    detail ??
                        "Passive discovery retained an observed object; input requires independent live validation."));
            }
            return new NativeReadDiscoveryResult(relations);
        }
    }
}
