import { useState } from "react";

const pages = {
  chat: "Chat",
  tools: "Tools",
  tasks: "Tasks",
  activity: "Activity",
  inference: "Inference Context",
  skills: "Skills",
  settings: "Memory & Settings",
} as const;

export type WorkspacePage = keyof typeof pages;
const tabs = Object.keys(pages) as WorkspacePage[];

export const workspacePanel = (page: WorkspacePage) => `workspace-${page}`;
const tabId = (page: WorkspacePage) => `${workspacePanel(page)}-tab`;

export function WorkspaceTabs({
  page,
  select,
}: {
  page: WorkspacePage;
  select: (page: WorkspacePage) => void;
}) {
  const [focused, setFocused] = useState<WorkspacePage | null>(null);
  return (
    <nav aria-label="Dashboard navigation">
      <div
        className="workspace-tabs"
        role="tablist"
        aria-label="Workspace"
        onBlur={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget))
            setFocused(null);
        }}
      >
        {tabs.map((tab, index) => (
          <button
            key={tab}
            id={tabId(tab)}
            type="button"
            role="tab"
            aria-controls={workspacePanel(tab)}
            aria-selected={page === tab}
            tabIndex={(focused ?? page) === tab ? 0 : -1}
            onFocus={() => setFocused(tab)}
            onClick={() => select(tab)}
            onKeyDown={(event) => {
              const next =
                event.key === "Home"
                  ? 0
                  : event.key === "End"
                    ? tabs.length - 1
                    : event.key === "ArrowRight"
                      ? (index + 1) % tabs.length
                      : event.key === "ArrowLeft"
                        ? (index - 1 + tabs.length) % tabs.length
                        : null;
              const nextTab = next === null ? undefined : tabs[next];
              if (!nextTab) return;
              event.preventDefault();
              document.getElementById(tabId(nextTab))?.focus();
            }}
          >
            {pages[tab]}
          </button>
        ))}
      </div>
    </nav>
  );
}

export function workspacePanelProps(tab: WorkspacePage, page: WorkspacePage) {
  return {
    role: "tabpanel",
    id: workspacePanel(tab),
    "aria-labelledby": tabId(tab),
    hidden: tab !== page,
    tabIndex: 0,
  };
}
