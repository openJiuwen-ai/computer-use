// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Linq;
using System.Runtime.InteropServices;
using System.Threading;

namespace ComputerUse.Windows
{
internal static class NativeValueReplacementTests
{
    sealed class Lease : IDisposable { public void Dispose() { } }

    // Each query port validates the same retained target and caller scope.
    // Metadata failures and state transitions are independent of ValuePattern.
    sealed class Provider : INativeObjectProvider, INativeValueReplacementObjectProvider
    {
        internal readonly string Scenario;
        internal string Value = "old", Role = "ControlType.Edit";
        internal bool? Enabled = true, ReadOnly = false, Password = false;
        internal bool Available = true, BrokenRole = false, BrokenCatalogue = false, BrokenLabel = false;
        internal bool BrokenEnabled = false, BrokenReadOnly = false, BrokenPattern = false;
        internal bool UnknownValue = false, BrokenPrivacy = false, Destroyed = false;
        internal bool MissingNarrowEvidence = false;
        internal string BoundaryError = null;
        internal int WholeReads = 0, NarrowReads = 0, Prepared = 0, Mutations = 0;
        internal int RoleReads = 0, CatalogueReads = 0, ValueReads = 0, BoundaryChecks = 0;
        internal readonly CancellationTokenSource Cancellation = new CancellationTokenSource();
        internal Provider(string scenario) { Scenario = scenario; }
        public IDisposable Acquire(Request request) { request.Check(); return new Lease(); }
        void Boundary(Dictionary<string, object> target, string[] within, Request request)
        {
            request.Check(); BoundaryChecks++;
            Check((string)target["node"] == "original" && (string)target["surface"] == "surface" &&
                  within.Length == 1 && within[0] == "original-ancestor", "A value query must retain its original target/scope");
            if (Destroyed) throw new Failure("stale_target", "the retained original object was destroyed");
            if (BoundaryError != null) throw new Failure(BoundaryError, "the original scope/modal boundary changed");
        }
        bool? ReadEnabled()
        {
            if (BrokenEnabled) throw new COMException("IsEnabled getter failed");
            return Enabled;
        }
        object ReadReadonly()
        {
            if (BrokenReadOnly) throw new COMException("ValuePattern.IsReadOnly getter failed");
            return ReadOnly;
        }
        object ReadPassword()
        {
            if (BrokenPrivacy) throw new COMException("optional privacy getter failed");
            return Password;
        }
        object ReadValue()
        {
            ValueReads++;
            if (UnknownValue) throw new COMException("optional value getter failed");
            return Value;
        }
        bool Pattern()
        {
            if (BrokenPattern) throw new COMException("the selected ValuePattern getter failed");
            return Available;
        }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        {
            WholeReads++; Boundary(target, within, request);
            RoleReads++;
            if (BrokenRole) throw new COMException("unrelated ControlType getter failed");
            var state = new NativeObjectState { Native = this, Role = Role,
                                               Label = BrokenLabel ? null : "Field", Enabled = ReadEnabled() };
            CatalogueReads++;
            if (BrokenCatalogue) throw new COMException("unrelated full pattern catalogue failed");
            state.ActionsComplete = true;
            state.Actions = new[] { "Invoke.Invoke" };
            state.Replaceable = false;
            if (Pattern()) NativeObjectValueObservation.Read(state, ReadReadonly, ReadPassword, ReadValue, request);
            Boundary(target, within, request);
            return state;
        }
        public NativeObjectState ReadValueReplacement(Dictionary<string, object> target, string[] within, Request request)
        {
            NarrowReads++; Boundary(target, within, request);
            var state = new NativeObjectState { Native = this,
                Enabled = NativeObjectValueObservation.Property(() => ReadEnabled(), request) as bool? };
            bool? available = NativeObjectValueObservation.Property(() => Pattern(), request) as bool?;
            state.Replaceable = available == false ? (bool?)false : null;
            if (available == true) NativeObjectValueObservation.Read(state, ReadReadonly, ReadPassword, ReadValue, request);
            if (MissingNarrowEvidence) state.Replaceable = null;
            Boundary(target, within, request);
            return state;
        }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        {
            request.Check(); Prepared++;
            Check(unit == "native_text_edit" && action == null, "The value unit must retain whole-control replacement semantics");
            if (!Pattern() || ReadOnly != false) throw new Failure("input_unavailable", "the selected ValuePattern is unavailable/read-only");
            if (Scenario == "catalogue_after_prepare") BrokenCatalogue = true;
            if (Scenario == "role_after_prepare") Role = "ControlType.Custom";
            if (Scenario == "disabled_after_prepare") Enabled = false;
            if (Scenario == "readonly_after_prepare") ReadOnly = true;
            if (Scenario == "pattern_after_prepare") Available = false;
            if (Scenario == "pattern_getter_after_prepare") BrokenPattern = true;
            if (Scenario == "destroyed_after_prepare") Destroyed = true;
            if (Scenario == "scope_after_prepare") BoundaryError = "stale_target";
            if (Scenario == "modal_after_prepare") BoundaryError = "blocked_by_modal";
            if (Scenario == "cancel_before_dispatch") Cancellation.Cancel();
            if (Scenario == "deadline_before_dispatch") request.Deadline = Wire.Now - 1;
            return () =>
            {
                Mutations++;
                if (Scenario != "noop") Value = Scenario == "formatted" ? "formatted-new" : text;
                if (Scenario == "label_after_mutation") BrokenLabel = true;
                if (Scenario == "role_after_mutation") Role = "ControlType.Custom";
                if (Scenario == "disabled_after_mutation") Enabled = false;
                if (Scenario == "readonly_after_mutation") ReadOnly = true;
                if (Scenario == "pattern_after_mutation") Available = false;
                if (Scenario == "pattern_getter_after_mutation") BrokenPattern = true;
                if (Scenario == "value_after_mutation") UnknownValue = true;
                if (Scenario == "destroyed_after_mutation") Destroyed = true;
                if (Scenario == "modal_after_mutation") BoundaryError = "blocked_by_modal";
                if (Scenario == "cancel_after_mutation") Cancellation.Cancel();
                if (Scenario == "deadline_after_mutation") request.Deadline = Wire.Now - 1;
                if (Scenario == "mutation_unknown") throw new COMException("setter entered; effect is unknown");
            };
        }
    }
    sealed class Legacy : INativeObjectProvider
    {
        internal readonly Provider Inner = new Provider("legacy");
        public IDisposable Acquire(Request request) { return Inner.Acquire(request); }
        public NativeObjectState Read(Dictionary<string, object> target, string[] within, Request request)
        { return Inner.Read(target, within, request); }
        public Action Prepare(NativeObjectState state, string unit, string action, string text, Request request)
        { return Inner.Prepare(state, unit, action, text, request); }
    }
    sealed class Operation
    {
        internal readonly Dictionary<string, object> Target = Wire.Obj(
            "session", "session", "instance", "instance", "rootWindow", "window", "surface", "surface",
            "node", "original", "capture", null);
        internal readonly NativeExecution Engine;
        internal readonly NativeWriteOperation Write;
        internal readonly Request Request;
        internal Dictionary<string, object> Primitive, Facts;
        internal Operation(INativeObjectProvider provider, CancellationToken token, string kind = "replace")
        {
            Request = new Request(token, 3000);
            Engine = new NativeExecution(provider);
            Write = new NativeWriteOperation(Target, Wire.Obj("kind", kind, "parameters",
                kind == "replace" ? Wire.Obj("text", "new") : Wire.Obj("action", "Invoke.Invoke")));
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
                Wire.Obj("kind", "native_text_edit", "primitiveId", Primitive["id"], "target", Target,
                         "foreground", false), Request));
        }
    }
    static void Check(bool condition, string message)
    {
        if (!condition) throw new Exception(message);
    }
    static void Configure(Provider provider, string scenario)
    {
        if (scenario == "role_initial") provider.BrokenRole = true;
        if (scenario == "catalogue_initial") provider.BrokenCatalogue = true;
        if (scenario == "label_initial") provider.BrokenLabel = true;
        if (scenario == "disabled_initial") provider.Enabled = false;
        if (scenario == "enabled_unknown") provider.Enabled = null;
        if (scenario == "enabled_getter") provider.BrokenEnabled = true;
        if (scenario == "readonly_initial") provider.ReadOnly = true;
        if (scenario == "readonly_unknown") provider.ReadOnly = null;
        if (scenario == "readonly_getter") provider.BrokenReadOnly = true;
        if (scenario == "pattern_missing") provider.Available = false;
        if (scenario == "pattern_getter") provider.BrokenPattern = true;
        if (scenario == "optional_value_unknown") provider.UnknownValue = true;
        if (scenario == "password") provider.Password = true;
        if (scenario == "privacy_unknown") provider.Password = null;
        if (scenario == "privacy_getter") provider.BrokenPrivacy = true;
        if (scenario == "missing_narrow_evidence") provider.MissingNarrowEvidence = true;
    }
    static void AfterInspect(Provider provider, string scenario)
    {
        if (scenario == "role_after_inspect") provider.Role = "ControlType.Custom";
        if (scenario == "role_getter_after_inspect") provider.BrokenRole = true;
        if (scenario == "catalogue_after_inspect") provider.BrokenCatalogue = true;
        if (scenario == "label_after_inspect") provider.BrokenLabel = true;
    }
    static void Positive(string scenario)
    {
        var provider = new Provider(scenario); Configure(provider, scenario);
        var operation = new Operation(provider, provider.Cancellation.Token);
        operation.Inspect(); AfterInspect(provider, scenario);
        var result = operation.Execute();
        Check((string)result["dispatch"] == "dispatched" && result["errorCode"] == null && provider.Mutations == 1,
              "An available original ValuePattern must be dispatched exactly once: " + scenario);
        Check(operation.Facts["nativeRole"] == null && !(bool)operation.Facts["actionsComplete"],
              "A whole-value query must not invent semantic role/catalogue evidence");
        Check(provider.WholeReads == 0 && provider.NarrowReads == 4 && provider.RoleReads == 0 && provider.CatalogueReads == 0,
              "Value replacement and its effect observation must not collect unrelated semantic metadata");
        bool uncertain = new[] { "optional_value_unknown", "password", "privacy_unknown", "privacy_getter",
            "formatted", "noop", "pattern_after_mutation", "pattern_getter_after_mutation", "value_after_mutation" }
            .Contains(scenario);
        Check((string)result["confirmation"] == (uncertain ? "unavailable" : "confirmed"),
              "Value confirmation must reflect actual observed text rather than the setter return code: " + scenario);
        if (scenario == "password" || scenario == "privacy_unknown" || scenario == "privacy_getter")
            Check(provider.ValueReads == 0, "Unknown/private values must not be read for confirmation");
        if (scenario == "noop") Check(((string)result["detail"]).Contains("remains unchanged"), "A no-op must remain explicit");
        if (scenario == "formatted") Check(((string)result["detail"]).Contains("could not be confirmed"), "Formatted values must not become literal confirmation");
    }
    static void Negative(string scenario)
    {
        var provider = new Provider(scenario); Configure(provider, scenario);
        var operation = new Operation(provider, provider.Cancellation.Token);
        Dictionary<string, object> result;
        try
        {
            operation.Inspect(); result = operation.Execute();
        }
        catch (Failure error)
        {
            result = Wire.Obj("dispatch", "notDispatched", "errorCode", error.Code);
        }
        catch (COMException)
        {
            result = Wire.Obj("dispatch", "notDispatched", "errorCode", "input_unavailable");
        }
        Check((string)result["dispatch"] == "notDispatched" && result["errorCode"] != null &&
              provider.Mutations == 0 && !operation.Write.Dispatched,
              "Missing permission/pattern or a changed original boundary must stop before mutation: " + scenario);
        if (scenario == "cancel_before_dispatch") Check((string)result["errorCode"] == "cancelled", "Cancellation must propagate");
        if (scenario == "deadline_before_dispatch") Check((string)result["errorCode"] == "deadline_exceeded", "Deadline must propagate");
    }
    static void AfterDispatchFailure(string scenario, string code, string dispatch)
    {
        var provider = new Provider(scenario);
        var operation = new Operation(provider, provider.Cancellation.Token);
        operation.Inspect();
        var result = operation.Execute();
        Check(provider.Mutations == 1 && operation.Write.Dispatched && (string)result["dispatch"] == dispatch &&
              (string)result["errorCode"] == code && result["retryReason"] == null,
              "An entered setter/changed original receiver must retain its effect and prohibit replay: " + scenario);
        if (scenario == "destroyed_after_mutation" || scenario == "modal_after_mutation")
            Check(provider.ValueReads == 3, "An invalid original boundary cannot authorize another value observation");
    }
    static void OtherIntentsAndProviders()
    {
        var provider = new Provider("action");
        var action = new Operation(provider, provider.Cancellation.Token, "action");
        action.Inspect();
        Check(provider.WholeReads == 1 && provider.NarrowReads == 0 && (bool)action.Facts["actionsComplete"],
              "An action must retain its own semantic snapshot rather than borrowing ValuePattern evidence");
        var legacy = new Legacy();
        var operation = new Operation(legacy, CancellationToken.None);
        operation.Inspect();
        legacy.Inner.BrokenCatalogue = true;
        var result = operation.Execute();
        Check((string)result["dispatch"] == "notDispatched" && legacy.Inner.Mutations == 0 && legacy.Inner.NarrowReads == 0,
              "An absent value-query port must not fabricate replacement evidence from another route");
    }
    static int failures = 0;
    static void Run(string name, Action test)
    {
        try { test(); Console.WriteLine("PASS\t" + name); }
        catch (Exception error) { failures++; Console.WriteLine("FAIL\t" + name + "\t" + error.GetType().Name + "\t" + error.Message); }
    }
    public static int Main()
    {
        foreach (string scenario in new[] { "healthy", "role_initial", "catalogue_initial", "label_initial",
            "role_after_inspect", "role_getter_after_inspect", "catalogue_after_inspect", "label_after_inspect",
            "catalogue_after_prepare", "role_after_prepare", "label_after_mutation", "role_after_mutation",
            "disabled_after_mutation", "readonly_after_mutation", "pattern_after_mutation", "pattern_getter_after_mutation",
            "optional_value_unknown", "value_after_mutation", "password", "privacy_unknown", "privacy_getter", "formatted", "noop" })
        {
            string current = scenario; Run("positive/" + current, () => Positive(current));
        }
        foreach (string scenario in new[] { "disabled_initial", "enabled_unknown", "enabled_getter", "readonly_initial",
            "readonly_unknown", "readonly_getter", "pattern_missing", "pattern_getter", "missing_narrow_evidence",
            "disabled_after_prepare", "readonly_after_prepare", "pattern_after_prepare", "pattern_getter_after_prepare",
            "destroyed_after_prepare", "scope_after_prepare", "modal_after_prepare", "cancel_before_dispatch", "deadline_before_dispatch" })
        {
            string current = scenario; Run("negative/" + current, () => Negative(current));
        }
        Run("after-dispatch/unknown", () => AfterDispatchFailure("mutation_unknown", "execution_unknown", "unknown"));
        Run("after-dispatch/identity", () => AfterDispatchFailure("destroyed_after_mutation", "stale_target", "dispatched"));
        Run("after-dispatch/modal", () => AfterDispatchFailure("modal_after_mutation", "blocked_by_modal", "dispatched"));
        Run("after-dispatch/cancel", () => AfterDispatchFailure("cancel_after_mutation", "cancelled", "dispatched"));
        Run("after-dispatch/deadline", () => AfterDispatchFailure("deadline_after_mutation", "deadline_exceeded", "dispatched"));
        Run("other-intents-and-provider", OtherIntentsAndProviders);
        return failures == 0 ? 0 : 1;
    }
}
}
