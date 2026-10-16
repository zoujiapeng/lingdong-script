/**
 * Host-side entry for the browser-only flow workbench plugin.
 *
 * The workbench is pure browser UI, so the host plane contributes nothing and
 * must not require the client bundle.
 * @module @lingdong/dsh-client-ui-lingdong
 */
/** Host plugin identity. */
export const name = "lingdong-client-ui";

/** No host services are consumed. */
export const inject = [];

/** Does nothing: the entire plugin lives on the client plane. */
export function apply() {}
