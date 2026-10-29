// Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

using System;
using System.Collections.Generic;
using System.Windows.Automation;

namespace ComputerUse.Windows
{
    internal sealed partial class NativeUiaKeyboard
    {
        readonly Dictionary<string, string> editors = new Dictionary<string, string>();
        static bool Layout(ControlType role)
        {
            return role == ControlType.Group || role == ControlType.Pane || role == ControlType.Custom;
        }

        bool OwnedEditor(UiaElement editor, NativeNodeRecord owner, Request r, List<UiaElement> layouts = null)
        {
            var seen = new HashSet<string>();
            var confirmedLayouts = new List<UiaElement>();
            for (var current = editor; current != null; current = UiaClient.Parent(current))
            {
                r.Check();
                string runtime = NativeReadStore.Runtime(current);
                if (String.IsNullOrEmpty(runtime) || !seen.Add(runtime) || seen.Count > 24)
                    return false;
                if (runtime == owner.Runtime)
                {
                    if (layouts != null)
                        layouts.AddRange(confirmedLayouts);
                    return true;
                }

                if (seen.Count > 1)
                {
                    var role = NativeUiaBoundary.Property(current, AutomationElement.ControlTypeProperty, r) as ControlType;
                    if (!Layout(role))
                        return false; // Do not cross another interactive owner.
                    confirmedLayouts.Add(current);
                }
            }

            return false;
        }

        void CollectEditors(UiaElement parent, Dictionary<string, UiaElement> found, HashSet<string> visited, Request r, int depth = 0)
        {
            if (depth > 6)
                throw new Failure("input_unavailable", "the internal editor relationship exceeds the supported layout depth");
            for (var child = UiaClient.FirstChild(parent); child != null; child = UiaClient.NextSibling(child))
            {
                r.Check();
                string runtime = NativeReadStore.Runtime(child);
                if (String.IsNullOrEmpty(runtime) || !visited.Add(runtime))
                    throw new Failure("input_unavailable", "the internal editor layout has an unconfirmed or repeated identity");
                if (visited.Count > 64)
                    throw new Failure("input_unavailable", "the internal editor layout exceeds the bounded resolution budget");
                var role = NativeUiaBoundary.Property(child, AutomationElement.ControlTypeProperty, r) as ControlType;
                if (role == ControlType.Edit || role == ControlType.Spinner)
                {
                    var capability = new NativeKeyboardState();
                    ReadTextCapability(child, capability, r);
                    if (capability.TextWritable == true && capability.TextSelection)
                        found[runtime] = child;
                }
                else if (Layout(role))
                    CollectEditors(child, found, visited, r, depth + 1);
            }
        }

        NativeNodeRecord ResolveEditor(NativeNodeRecord owner, UiaElement focused, Request r)
        {
            var found = new Dictionary<string, UiaElement>();
            var layouts = new List<UiaElement>();
            string remembered;
            if (editors.TryGetValue(owner.Id, out remembered))
            {
                try
                {
                    var cached = store.Node(remembered, owner.Surface, r);
                    if (OwnedEditor(cached.Element, owner, r, layouts))
                        found[cached.Runtime] = cached.Element;
                    else
                        editors.Remove(owner.Id);
                }
                catch (Failure error)
                {
                    if (error.Code != "stale_target" && error.Code != "observation_incomplete")
                        throw;
                    editors.Remove(owner.Id);
                }
            }

            if (focused != null && NativeReadStore.Runtime(focused) != owner.Runtime)
            {
                var role = NativeUiaBoundary.Property(focused, AutomationElement.ControlTypeProperty, r) as ControlType;
                if ((role == ControlType.Edit || role == ControlType.Spinner) && OwnedEditor(focused, owner, r, layouts))
                {
                    var capability = new NativeKeyboardState();
                    ReadTextCapability(focused, capability, r);
                    if (capability.TextWritable == true && capability.TextSelection)
                        found[NativeReadStore.Runtime(focused)] = focused;
                }
            }

            // A provider may omit its editor from the owner's forward children.
            // A confirmed focused leaf supplies layout parents, whose siblings
            // must still be examined so that two editors cannot become one.
            CollectEditors(owner.Element, found, new HashSet<string>(), r);
            var scanned = new HashSet<string>();
            foreach (var layout in layouts)
                if (scanned.Add(NativeReadStore.Runtime(layout)))
                    CollectEditors(layout, found, new HashSet<string>(), r);
            if (found.Count > 1)
                throw new Failure("ambiguous_target", "The selected composite contains more than one independent text editor; select a specific editor. No text was sent.");
            foreach (var element in found.Values)
            {
                if (!OwnedEditor(element, owner, r))
                    throw new Failure("input_unavailable", "the internal editor no longer belongs to the original control");
                string id = store.Keep(element, owner.Window, r);
                if (editors.Count > 256)
                    editors.Clear();
                editors[owner.Id] = id;
                return store.Node(id, owner.Surface, r);
            }

            return null;
        }

        NativeNodeRecord ValidateEditor(Target target, Request r)
        {
            var owner = store.Node(target.Node.Id, target.Node.Surface, r);
            var editor = store.Node(target.Editor.Id, target.Editor.Surface, r);
            if (!OwnedEditor(editor.Element, owner, r))
                throw new Failure("input_unavailable", "the internal editor left its original control; input stopped");
            return editor;
        }
    }
}
