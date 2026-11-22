import { useState, type ReactNode } from "react";

const pages = {
  conversation: "Conversation",
  activity: "Activity",
  inference: "Inference context",
  memory: "Memory sources",
  prompts: "Prompts",
  tasks: "Tasks",
  mcp: "Run an MCP tool",
  skills: "Skills",
  providers: "Tools & providers",
  voice: "Voice and speaking speed",
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

export function WorkspacePanel({
  view,
  page,
  description,
  children,
}: {
  view: WorkspacePage;
  page: WorkspacePage;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section
      role="tabpanel"
      id={workspacePanel(view)}
      aria-labelledby={tabId(view)}
      hidden={view !== page}
      tabIndex={0}
    >
      <div className="page-heading">
        <h2>{pages[view]}</h2>
        {description}
      </div>
      {children}
    </section>
  );
}
