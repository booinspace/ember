import { useState } from "react";
import {
  control,
  libraryIds,
  recallSelection,
  captureControl,
  runtimeState,
  type RuntimeState,
  type RecallSelection,
} from "./contracts";

export function Controls({
  state,
  update,
  failure,
}: {
  state: RuntimeState;
  update: (state: RuntimeState) => void;
  failure: (message: string) => void;
}) {
  const [libraries, setLibraries] = useState(
    state.recall.included_libraries.join(", "),
  );
  const [saving, setSaving] = useState(false);
  const save = async (patch: Partial<RecallSelection>): Promise<void> => {
    setSaving(true);
    try {
      const recall = await control("recall", recallSelection, {
        ...state.recall,
        ...patch,
      });
      update({ ...state, recall });
    } catch (error: unknown) {
      failure(
        error instanceof Error ? error.message : "Source control unavailable.",
      );
    } finally {
      setSaving(false);
    }
  };
  const capture = async (enabled: boolean): Promise<void> => {
    setSaving(true);
    try {
      const value = await control("capture", captureControl, {
        ...state.capture,
        enabled,
      });
      update({ ...state, capture: value });
    } catch {
      failure(
        "Capture control unavailable. Refresh to check the current setting.",
      );
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="controls" aria-label="Memory controls">
      <h3>Memory sources</h3>
      <p>
        <a href="/memory-graph">Memory graph</a>
      </p>
      <p>
        Choose what Ember may recall. Changing sources does not approve imports.
      </p>
      <label className="toggle">
        <input
          type="checkbox"
          checked={state.recall.local_enabled}
          disabled={saving}
          onChange={(event) =>
            void save({ local_enabled: event.target.checked })
          }
        />
        Character memories
      </label>
      <label>
        Imported libraries
        <select
          value={state.recall.import_mode}
          disabled={saving}
          onChange={(event) => {
            const mode = recallSelection.shape.import_mode.parse(
              event.target.value,
            );
            void save({ import_mode: mode });
          }}
        >
          <option value="all">All approved libraries</option>
          <option value="none">None</option>
          <option value="selected">Selected libraries</option>
        </select>
      </label>
      {state.recall.import_mode === "selected" && (
        <>
          {state.libraries.map((library) => (
            <label className="toggle" key={library.library_id}>
              <input
                type="checkbox"
                disabled={saving}
                checked={state.recall.included_libraries.includes(
                  library.library_id,
                )}
                onChange={(event) => {
                  const included = event.target.checked
                    ? [...state.recall.included_libraries, library.library_id]
                    : state.recall.included_libraries.filter(
                        (id) => id !== library.library_id,
                      );
                  setLibraries(included.join(", "));
                  void save({
                    included_libraries: included,
                    excluded_libraries: state.recall.excluded_libraries.filter(
                      (id) => id !== library.library_id,
                    ),
                  });
                }}
              />
              {library.namespace}
              {library.available ? "" : " (unavailable)"}
            </label>
          ))}
          <label>
            Library IDs
            <textarea
              value={libraries}
              disabled={saving}
              onChange={(event) => setLibraries(event.target.value)}
              placeholder="Comma-separated library IDs"
            />
          </label>
          <button
            disabled={saving}
            onClick={() => {
              try {
                void save({ included_libraries: libraryIds(libraries) });
              } catch {
                failure("Enter valid library IDs.");
              }
            }}
          >
            Apply selection
          </button>
        </>
      )}
      <h3>Conversation capture</h3>
      <label className="toggle">
        <input
          type="checkbox"
          checked={state.capture.enabled}
          disabled={saving}
          onChange={(event) => void capture(event.target.checked)}
        />
        Learn from our conversations
      </label>
      <p>
        Native proposals use the acceptance policy. Pending work runs when
        realtime conversation is released.
      </p>
      <h3>Memory processing</h3>
      <p>
        Index: {state.processing.index_status}. Queued:{" "}
        {state.processing.counts.queued ?? 0}; paused:{" "}
        {state.processing.counts.paused ?? 0}; failed:{" "}
        {state.processing.counts.failed ?? 0}.
      </p>
      <p>
        Accepted: {state.processing.counts.accepted ?? 0}; supported:{" "}
        {state.processing.counts.supported ?? 0}; needs review:{" "}
        {state.processing.counts.deferred ?? 0}; rejected:{" "}
        {state.processing.counts.rejected ?? 0}.
      </p>
      {state.processing.failure_codes.length > 0 && (
        <p role="status">
          Failed work: {state.processing.failure_codes.join(", ")}. Inspect and
          explicitly retry its checkpoint with ember-memory.
        </p>
      )}
      <button
        disabled={saving}
        onClick={() => {
          setSaving(true);
          void control("state", runtimeState)
            .then((value) => {
              setLibraries(value.recall.included_libraries.join(", "));
              update(value);
            })
            .catch(() => failure("Memory status is unavailable."))
            .finally(() => setSaving(false));
        }}
      >
        Refresh memory status
      </button>
      <p className="identity">Character {state.binding.character_id}</p>
    </section>
  );
}
