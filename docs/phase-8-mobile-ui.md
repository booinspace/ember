# Phase 8 mobile browser layout

The existing browser application owns the conversation, connection, memory controls
and recall interactions. This change adapts their presentation to small and short
screens: the header reflows, the message pane fits the viewport, composer actions
have larger touch targets, form controls avoid mobile input zoom, and safe-area
insets protect content near screen cutouts. The LAN link field wraps and remains
selectable. The same actions, authorization and runtime contracts apply at every
size; no backend, persistence or network capability is added.

Connection state has a status role, the composer and memory controls have accessible
names, and the microphone button exposes its pressed state. Existing keyboard send,
voice degradation, memory and task behavior remain in place. Synthetic browser tests
cover sending, long content, LAN link display, horizontal overflow, mobile button
size and accessible labels at phone, landscape, tablet and desktop widths. The
isolated test build leaves the live frontend untouched. Real mobile browsers, soft
keyboards and physical audio remain separate acceptance checks.
