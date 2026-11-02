// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
using System.Threading;

namespace ComputerUse.Windows
{
internal static class NativeExplicitActionTests
{
    static readonly string[] EnabledActions = {
        "Invoke.Invoke", "Toggle.Toggle", "SelectionItem.Select", "SelectionItem.AddToSelection",
        "SelectionItem.RemoveFromSelection", "ExpandCollapse.Expand", "ExpandCollapse.Collapse", "Window.Close"
    };

    sealed class Provider : INativeObjectProvider, INativeExactActionObjectProvider, INativeExpansionObjectProvider
    {
        internal bool ThrowRole, ThrowCatalogue, CatalogueComplete = true, Available = true, Destroyed, ThrowMutation;
        internal bool MissingEvidence, ForgedSnapshotWhenMissing, MisleadingExactInWholeRead, ThrowTypedMutation;
        internal bool ThrowEnabled;
        internal bool? Enabled = true, Collapsed = true;
        internal string Role = "ControlType.Button", EvidenceName, BoundaryFailure;
        internal string[] Actions = { "Invoke.Invoke" };
        internal int WholeReads, ExactReads, BoundaryChecks, Prepared, Mutations, EnabledReads;
        internal Action AfterPrepare;
        public IDisposable Acquire(Request request)
        {
            request.Check();
            return new Lease();
        }
        void Boundary(Request request)
        {
            request.Check();
            BoundaryChecks++;
            if (Destroyed)
                throw new Failure("stale_target", "the original target was destroyed");
            if (BoundaryFailure != null)
                throw new Failure(BoundaryFailure, "the retained target/scope/modal boundary changed");
        }
        bool? ReadEnabled(Request request)
        {
            request.Check();
            EnabledReads++;
            if (ThrowEnabled)
                throw new COMException("the independent IsEnabled getter failed", unchecked((int)0x80004005));
            return Enabled;
        }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        {
            WholeReads++;
            if (ThrowRole)
                throw new COMException("unrelated ControlType getter failed");
            if (ThrowCatalogue)
                throw new COMException("unrelated pattern availability getter failed");
            Boundary(request);
            return new NativeObjectState { Native = this, Role = Role, Enabled = ReadEnabled(request),
                                           ActionsComplete = CatalogueComplete, Actions = Actions,
                                           CollapsedExpansion = Collapsed,
                                           ExactAction = MisleadingExactInWholeRead
                                                             ? new NativeExactActionEvidence("Invoke.Invoke", true) : null };
        }
        public NativeObjectState ReadExpansion(Dictionary<string, object> target, string[] within, Request request)
        {
            return Read(target, within, request);
        }
        public NativeObjectState ReadAction(Dictionary<string, object> target, string[] within, string action,
                                           Request request)
        {
            ExactReads++;
            var state = NativeExactActionObservation.Read(this, EvidenceName ?? action, () => ReadEnabled(request),
                () => Available && (action == "ScrollItem.ScrollIntoView" || Array.IndexOf(EnabledActions, action) >= 0),
                () => Boundary(request), request);
            if (MissingEvidence)
            {
                state.ExactAction = null;
                if (ForgedSnapshotWhenMissing)
                {
                    state.ActionsComplete = true;
                    state.Actions = new[] { action };
                }
            }
            return state;
        }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        {
            request.Check();
            Prepared++;
            if (AfterPrepare != null)
                AfterPrepare();
            return () =>
            {
                Mutations++;
                if (ThrowTypedMutation)
                    throw new Failure("input_unavailable", "native action failed after entry");
                if (ThrowMutation)
                    throw new COMException("mutation failed after entry; effect is unknown");
                if (action == "Window.Close")
                    Destroyed = true;
            };
        }
    }

    sealed class Lease : IDisposable
    {
        public void Dispose() { }
    }

    sealed class Reader : INativeReadBackend
    {
        public void Validate(Dictionary<string, object> target, Request request) { request.Check(); }
        public object Inspect(Dictionary<string, object> target, Dictionary<string, object> query, Request request)
        {
            throw new Exception("An input-context query must not collect an unrelated UIA tree");
        }
        public void Release() { }
    }

    // An existing provider without exact-pattern evidence must retain its
    // complete semantic checks; absence of the new port grants no permission.
    sealed class Legacy : INativeObjectProvider
    {
        internal readonly Provider Inner = new Provider();
        public IDisposable Acquire(Request request) { return Inner.Acquire(request); }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        {
            return Inner.Read(target, within, request);
        }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        {
            return Inner.Prepare(state, unit, action, text, request);
        }
    }

    sealed class Operation
    {
        internal readonly Dictionary<string, object> Target = Wire.Obj(
            "session", "session", "instance", "instance", "rootWindow", "window", "surface", "surface",
            "node", "original", "capture", null);
        internal readonly NativeExecution Engine;
        internal readonly NativeWriteOperation Write;
        internal readonly Request Request = new Request(CancellationToken.None, 3000);
        internal Dictionary<string, object> Primitive, Facts;
        internal Operation(INativeObjectProvider provider, string kind = "action", string action = "Invoke.Invoke")
        {
            Engine = new NativeExecution(provider);
            Write = new NativeWriteOperation(Target,
                Wire.Obj("kind", kind, "parameters", kind == "action" ? Wire.Obj("action", action) : Wire.Obj()));
        }
        internal void Inspect()
        {
            Engine.Validate(Write.Intent, Target);
            Facts = Wire.Object(Engine.Inspect(Write, Target, new[] { "original-ancestor" }, Request));
            Primitive = Wire.Object(Wire.Array(Facts["primitives"])[0]);
        }
        internal Dictionary<string, object> Execute()
        {
            return Wire.Object(Engine.Execute(Write,
                Wire.Obj("kind", "native_action", "primitiveId", Primitive["id"], "target", Target,
                         "foreground", false), Request));
        }
    }

    static void Check(bool condition, string message)
    {
        if (!condition)
            throw new Exception(message);
    }
    static void Refused(Operation operation, Provider provider, string message)
    {
        var result = operation.Execute();
        Check(result["errorCode"] != null && (string)result["dispatch"] == "notDispatched" &&
              !operation.Write.Dispatched && provider.Mutations == 0, message);
    }

    static void ExplicitDoesNotReadUnrelatedMetadata()
    {
        foreach (string failure in new[] { "role", "catalogue" })
        {
            var provider = new Provider { ThrowRole = failure == "role", ThrowCatalogue = failure == "catalogue" };
            var operation = new Operation(provider, action: "ScrollItem.ScrollIntoView");
            operation.Inspect();
            Check((bool)operation.Facts["actionsComplete"] == false && operation.Facts["nativeRole"] == null,
                  "An exact query must not claim a complete catalogue or invent a native role");
            var result = operation.Execute();
            Check(result["errorCode"] == null && (string)result["dispatch"] == "dispatched" &&
                  provider.Mutations == 1 && provider.WholeReads == 0 && provider.ExactReads == 3 &&
                  provider.BoundaryChecks == 3,
                  "Explicit action must query only its own pattern and dispatch once under final boundary checks");
        }
    }

    static void ExplicitDispatchDoesNotRequireSemanticSnapshot()
    {
        foreach (string change in new[] { "roleUnknown", "roleGetter", "catalogueGetter", "incompleteCatalogue" })
        {
            var provider = new Provider();
            var operation = new Operation(provider);
            operation.Inspect();
            if (change == "roleUnknown") provider.Role = null;
            if (change == "roleGetter") provider.ThrowRole = true;
            if (change == "catalogueGetter") provider.ThrowCatalogue = true;
            if (change == "incompleteCatalogue") provider.CatalogueComplete = false;
            var result = operation.Execute();
            Check(result["errorCode"] == null && (string)result["dispatch"] == "dispatched" && provider.Mutations == 1,
                  "ObjectPipeline explicit dispatch must consume exact capability rather than unrelated semantic metadata: " + change);
        }
    }

    static void ExactEvidenceRequired()
    {
        foreach (string change in new[] { "unsupported", "mismatch", "missing", "disabled", "unknownEnabled" })
        {
            var provider = new Provider();
            var operation = new Operation(provider);
            operation.Inspect();
            if (change == "unsupported") provider.Available = false;
            if (change == "mismatch") provider.EvidenceName = "Toggle.Toggle";
            if (change == "missing") provider.MissingEvidence = true;
            if (change == "disabled") provider.Enabled = false;
            if (change == "unknownEnabled") provider.Enabled = null;
            Refused(operation, provider, "Exact action availability, name and enabled permission remain mandatory");
            Check(provider.Prepared == 0, "Invalid exact evidence must fail before acquiring a mutation");
        }
        var absent = new Provider { Available = false, ThrowRole = true, ThrowCatalogue = true };
        var unsupported = new Operation(absent);
        unsupported.Inspect();
        Check((string)unsupported.Primitive["supported"] == "no" && absent.WholeReads == 0,
              "An absent requested pattern must not be invented from role or other action declarations");
        Refused(unsupported, absent, "An unsupported exact action must never enter its mutation");
    }

    static void BoundIntentAndTargetRemainFixed()
    {
        foreach (bool changeTarget in new[] { false, true })
        {
            var provider = new Provider();
            var operation = new Operation(provider);
            operation.Inspect();
            if (changeTarget) operation.Target["node"] = "another-control";
            else operation.Write.Contracts[(string)operation.Primitive["id"]].Action = "Toggle.Toggle";
            Refused(operation, provider, "Exact action evidence cannot retarget the binding or change its original method");
            Check(provider.ExactReads == 1 && provider.Prepared == 0,
                  "A changed target/action contract must fail before a native provider callback");
        }
    }

    static void FinalGuardRetainsNativeBoundaries()
    {
        foreach (string change in new[] { "pattern", "disabled", "identity", "scope", "blocked_by_modal" })
        {
            var provider = new Provider();
            var operation = new Operation(provider);
            operation.Inspect();
            provider.AfterPrepare = () =>
            {
                if (change == "pattern") provider.Available = false;
                else if (change == "disabled") provider.Enabled = false;
                else provider.BoundaryFailure = change == "identity" || change == "scope" ? "stale_target" : change;
            };
            Refused(operation, provider, "Pattern acquisition callbacks must not bypass the final exact/boundary guard");
            Check(provider.Prepared == 1, "The final guard must run after mutation preparation, before entry");
        }
    }

    static void FinalMissingEvidenceCannotUseSnapshot()
    {
        var provider = new Provider();
        var operation = new Operation(provider);
        operation.Inspect();
        provider.AfterPrepare = () =>
        {
            provider.MissingEvidence = true;
            provider.ForgedSnapshotWhenMissing = true;
        };
        Refused(operation, provider,
                "A final exact-action query without typed evidence must not fall back to a complete-looking snapshot");
        Check(provider.Prepared == 1 && provider.ExactReads == 3,
              "The missing-evidence regression must reach the final query after mutation preparation");
    }

    static void SemanticRequirementsStayStrict()
    {
        foreach (string kind in new[] { "click", "openMenu" })
            foreach (string change in new[] { "role", "catalogue" })
            {
                var provider = new Provider();
                var operation = new Operation(provider, kind);
                operation.Inspect();
                provider.MisleadingExactInWholeRead = true;
                if (change == "role") provider.Role = null;
                else provider.CatalogueComplete = false;
                Refused(operation, provider, "Automatic click/menu semantics must retain role and complete actions");
                Check(provider.ExactReads == 0, "Automatic semantics must never use the explicit-action query port");
            }
        foreach (bool expansion in new[] { false, true })
        {
            var provider = new Provider();
            var operation = new Operation(provider, "click");
            operation.Inspect();
            var contract = operation.Write.Contracts[(string)operation.Primitive["id"]];
            contract.FixedClickChoice = true;
            if (expansion)
            {
                contract.Action = "ExpandCollapse.Expand";
                contract.RequireCollapsedExpansion = true;
                provider.Actions = new[] { "ExpandCollapse.Expand" };
                provider.Collapsed = false;
            }
            else provider.Actions = new[] { "Invoke.Invoke", "ExpandCollapse.Expand" };
            Refused(operation, provider, "Frozen Invoke/Expand click semantics must not be weakened");
        }
    }

    static void LegacyProviderKeepsSemanticChecks()
    {
        var provider = new Legacy();
        var operation = new Operation(provider);
        operation.Inspect();
        provider.Inner.MisleadingExactInWholeRead = true;
        provider.Inner.Role = null;
        Refused(operation, provider.Inner, "A provider without exact evidence must retain full semantic validation");
    }

    static void MutationFailureIsTerminal()
    {
        var provider = new Provider { ThrowMutation = true };
        var operation = new Operation(provider);
        operation.Inspect();
        var result = operation.Execute();
        Check((string)result["dispatch"] == "unknown" && operation.Write.Dispatched && operation.Write.InputTerminated &&
              result["retryReason"] == null && provider.Mutations == 1 && provider.ExactReads == 3,
              "An entered explicit mutation must remain uncertain and terminal, without fallback or replay");
    }

    static void RuntimeNeverReplaysEnteredMutation()
    {
        foreach (bool typedFailure in new[] { false, true })
        {
            var provider = new Provider { ThrowRole = true, ThrowCatalogue = true,
                                          ThrowMutation = !typedFailure, ThrowTypedMutation = typedFailure };
            var runtime = new NativeRuntime("session", new Reader(), new NativeExecution(provider));
            var target = Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window",
                                  "surface", "surface", "node", "original", "capture", null);
            var intent = Wire.Obj("kind", "action", "parameters", Wire.Obj("action", "Invoke.Invoke"));
            Func<Request> request = () => new Request(CancellationToken.None, 3000);
            try
            {
                var begun = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "write", "target", target,
                                                                         "intent", intent), request()));
                var token = begun["operation"];
                var facts = Wire.Object(runtime.Perform("inspect",
                    Wire.Obj("operation", token, "target", target,
                             "query", Wire.Obj("kind", "inputContext", "within", new string[0])), request()));
                var primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
                var command = Wire.Obj("kind", "native_action", "primitiveId", primitive["id"], "target", target,
                                       "foreground", false);
                var parameters = Wire.Obj("operation", token, "step", 1, "command", command);
                var result = Wire.Object(runtime.Perform("execute", parameters, request()));
                Check((string)result["dispatch"] == "unknown" && result["retryReason"] == null,
                      "A mutation exception must not regain pre-dispatch retry permission");
                var same = Wire.Object(runtime.Perform("execute", parameters, request()));
                Check(Object.ReferenceEquals(result, same) && provider.Mutations == 1 && provider.ExactReads == 3,
                      "A duplicate step must return its original uncertain receipt without repeating the action");
                parameters["step"] = 2;
                bool refused = false;
                try { runtime.Perform("execute", parameters, request()); }
                catch (Failure error) { refused = error.Code == "execution_unknown"; }
                Check(refused && provider.Mutations == 1 && provider.ExactReads == 3,
                      "A new step cannot replay an explicit action after the mutation may have run");
            }
            finally { runtime.Close(); }
        }
    }

    static void CloseNeedsNoPostMutationRead()
    {
        var provider = new Provider();
        var operation = new Operation(provider, action: "Window.Close");
        operation.Inspect();
        var result = operation.Execute();
        Check(result["errorCode"] == null && (string)result["dispatch"] == "dispatched" && provider.Destroyed &&
              provider.ExactReads == 3 && provider.Mutations == 1,
              "A successful explicit action may destroy its original target without a post-mutation read");
    }

    static void SetEnabledCase(Provider provider, string value)
    {
        if (value == "false") provider.Enabled = false;
        else if (value == "unknown") provider.Enabled = null;
        else provider.ThrowEnabled = true;
    }

    static void ExplicitScrollItemDoesNotRequireEnabled(string enabled)
    {
        var provider = new Provider { ThrowRole = true, ThrowCatalogue = true };
        SetEnabledCase(provider, enabled);
        var operation = new Operation(provider, action: "ScrollItem.ScrollIntoView");
        operation.Inspect();
        Check((string)operation.Facts["enabled"] == "unknown" &&
              (string)operation.Primitive["supported"] == "yes" &&
              (string)operation.Primitive["readiness"] == "yes" &&
              Wire.Array(operation.Primitive["pending"]).Length == 0 && provider.EnabledReads == 0,
              "Explicit ScrollIntoView must retain unknown enabled observation and offer a ready exact action: " + enabled);
        var result = operation.Execute();
        Check(result["errorCode"] == null && (string)result["dispatch"] == "dispatched" &&
              operation.Write.Dispatched && provider.Prepared == 1 && provider.Mutations == 1 &&
              provider.WholeReads == 0 && provider.ExactReads == 3 && provider.BoundaryChecks == 3 &&
              provider.EnabledReads == 0,
              "Only explicit ScrollIntoView may dispatch without querying enabled permission: " + enabled);
    }

    static void OtherActionsKeepEnabledPermission()
    {
        foreach (string action in EnabledActions)
            foreach (string enabled in new[] { "false", "unknown", "getterError" })
                foreach (bool legacy in new[] { false, true })
                {
                    var provider = legacy ? new Legacy() : null;
                    var inner = legacy ? provider.Inner : new Provider();
                    inner.Actions = new[] { action };
                    var operation = new Operation(legacy ? (INativeObjectProvider)provider : inner, action: action);
                    operation.Inspect();
                    Check((string)operation.Primitive["supported"] == "yes",
                          "The permission regression must begin with the requested pattern actually available: " + action);
                    SetEnabledCase(inner, enabled);
                    Refused(operation, inner,
                            "Other named methods retain enabled permission for exact and legacy providers: " + action + "/" + enabled);
                    Check(inner.Prepared == 0 && inner.EnabledReads == 2,
                          "Unavailable enabled permission must fail before preparing another method: " + action);
                }
    }

    static void LegacyScrollItemKeepsItsWholeSnapshot()
    {
        foreach (string enabled in new[] { "false", "unknown" })
        {
            var provider = new Legacy();
            provider.Inner.Actions = new[] { "ScrollItem.ScrollIntoView" };
            SetEnabledCase(provider.Inner, enabled);
            var operation = new Operation(provider, action: "ScrollItem.ScrollIntoView");
            operation.Inspect();
            Check((string)operation.Primitive["supported"] == "yes" &&
                  (string)operation.Primitive["readiness"] == "yes" &&
                  Wire.Array(operation.Primitive["pending"]).Length == 0,
                  "Explicit legacy ScrollIntoView must not acquire an enabled prerequisite: " + enabled);
            var result = operation.Execute();
            Check(result["errorCode"] == null && (string)result["dispatch"] == "dispatched" &&
                  provider.Inner.Mutations == 1 && provider.Inner.WholeReads == 3 && provider.Inner.ExactReads == 0,
                  "Legacy ScrollIntoView keeps full snapshot validation without requiring enabled: " + enabled);
        }
        foreach (string failure in new[] { "getter", "role", "catalogue" })
        {
            var provider = new Legacy();
            provider.Inner.Actions = new[] { "ScrollItem.ScrollIntoView" };
            var operation = new Operation(provider, action: "ScrollItem.ScrollIntoView");
            operation.Inspect();
            if (failure == "getter") provider.Inner.ThrowEnabled = true;
            else if (failure == "role") provider.Inner.Role = null;
            else provider.Inner.CatalogueComplete = false;
            Refused(operation, provider.Inner,
                    "The ScrollItem permission exception cannot invent a legacy narrow query or weaken its snapshot: " + failure);
        }
    }

    static void ScrollItemPermissionDoesNotWeakenAutomaticSemantics()
    {
        foreach (string kind in new[] { "click", "openMenu" })
            foreach (string enabled in new[] { "false", "unknown", "getterError" })
            {
                var provider = new Provider { Actions = new[] { "ScrollItem.ScrollIntoView" } };
                var operation = new Operation(provider, kind);
                operation.Inspect();
                SetEnabledCase(provider, enabled);
                Refused(operation, provider,
                        "An automatic click/menu contract must not inherit explicit ScrollItem permission: " + kind);
                Check(provider.ExactReads == 0 && provider.Prepared == 0,
                      "Automatic semantics must remain on their original whole-snapshot checks");
            }
    }

    static void ScrollItemFinalGuardRetainsBoundaries()
    {
        foreach (string change in new[] { "pattern", "identity", "scope", "blocked_by_modal" })
        {
            var provider = new Provider();
            var operation = new Operation(provider, action: "ScrollItem.ScrollIntoView");
            operation.Inspect();
            provider.AfterPrepare = () =>
            {
                if (change == "pattern") provider.Available = false;
                else provider.BoundaryFailure = change == "identity" || change == "scope" ? "stale_target" : change;
            };
            Refused(operation, provider,
                    "ScrollIntoView still checks its exact pattern, identity, scope and modality after preparation: " + change);
            Check(provider.Prepared == 1 && provider.ExactReads == 3 && provider.BoundaryChecks == 3,
                  "The ScrollIntoView boundary regression must reach the final native query before mutation");
        }
    }

    static void ScrollItemMutationFailureIsTerminal()
    {
        foreach (bool typedFailure in new[] { false, true })
        {
            var provider = new Provider { ThrowEnabled = true, ThrowMutation = !typedFailure,
                                          ThrowTypedMutation = typedFailure };
            var runtime = new NativeRuntime("session", new Reader(), new NativeExecution(provider));
            var target = Wire.Obj("session", "session", "instance", "instance", "rootWindow", "window",
                                  "surface", "surface", "node", "original", "capture", null);
            var intent = Wire.Obj("kind", "action", "parameters", Wire.Obj("action", "ScrollItem.ScrollIntoView"));
            Func<Request> request = () => new Request(CancellationToken.None, 3000);
            try
            {
                var begun = Wire.Object(runtime.Perform("begin", Wire.Obj("mode", "write", "target", target,
                                                                         "intent", intent), request()));
                var token = begun["operation"];
                var facts = Wire.Object(runtime.Perform("inspect", Wire.Obj("operation", token, "target", target,
                    "query", Wire.Obj("kind", "inputContext", "within", new string[0])), request()));
                var primitive = Wire.Object(Wire.Array(facts["primitives"])[0]);
                var command = Wire.Obj("kind", "native_action", "primitiveId", primitive["id"], "target", target,
                                       "foreground", false);
                var parameters = Wire.Obj("operation", token, "step", 1, "command", command);
                var result = Wire.Object(runtime.Perform("execute", parameters, request()));
                Check((string)result["dispatch"] == "unknown" && result["retryReason"] == null &&
                      provider.Mutations == 1 && provider.EnabledReads == 0,
                      "An entered ScrollIntoView failure remains terminal without consulting enabled permission");
                var same = Wire.Object(runtime.Perform("execute", parameters, request()));
                Check(Object.ReferenceEquals(result, same) && provider.Mutations == 1 && provider.ExactReads == 3,
                      "A repeated ScrollIntoView step must return its original uncertain receipt");
                parameters["step"] = 2;
                bool refused = false;
                try { runtime.Perform("execute", parameters, request()); }
                catch (Failure error) { refused = error.Code == "execution_unknown"; }
                Check(refused && provider.Mutations == 1 && provider.ExactReads == 3,
                      "ScrollIntoView cannot regain replay permission after an entered mutation failure");
            }
            finally { runtime.Close(); }
        }
    }

    static void ScrollItemPermissionCases()
    {
        int failures = 0;
        Action<string, Action> run = (name, action) =>
        {
            try { action(); Console.WriteLine("ScrollItem permission passed: " + name); }
            catch (Exception error)
            {
                failures++;
                Console.WriteLine("ScrollItem permission failed: " + name + ": " + error.Message);
            }
        };
        run("other eight methods retain enabled permission", OtherActionsKeepEnabledPermission);
        run("automatic semantics retain enabled permission", ScrollItemPermissionDoesNotWeakenAutomaticSemantics);
        run("final native boundaries", ScrollItemFinalGuardRetainsBoundaries);
        foreach (string enabled in new[] { "false", "unknown", "getterError" })
        {
            string value = enabled;
            run("explicit enabled=" + value, () => ExplicitScrollItemDoesNotRequireEnabled(value));
        }
        run("legacy snapshot boundaries", LegacyScrollItemKeepsItsWholeSnapshot);
        run("entered mutation failure is terminal", ScrollItemMutationFailureIsTerminal);
        Check(failures == 0, "ScrollItem permission regressions failed: " + failures);
    }

    static void Main()
    {
        FinalMissingEvidenceCannotUseSnapshot();
        ExplicitDispatchDoesNotRequireSemanticSnapshot();
        ExplicitDoesNotReadUnrelatedMetadata();
        ExactEvidenceRequired();
        BoundIntentAndTargetRemainFixed();
        FinalGuardRetainsNativeBoundaries();
        SemanticRequirementsStayStrict();
        LegacyProviderKeepsSemanticChecks();
        MutationFailureIsTerminal();
        RuntimeNeverReplaysEnteredMutation();
        CloseNeedsNoPostMutationRead();
        ScrollItemPermissionCases();
        Console.WriteLine("NativeExplicitActionTests: passed (exact pattern evidence, semantic isolation, final guards, no replay)");
    }
}
}
