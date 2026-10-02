/**
 * Private work: commands run where nothing leads off this computer.
 *
 * A network namespace alone is not enough: the desktop's message bus is a Unix
 * socket, and through it a command could ask another program, such as the
 * browser, to open a web address with data in it. So bubblewrap hides the
 * sockets of the desktop session, the system bus and X11 as well, and leaves
 * the files as they are.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

const BWRAP = (process.env.PATH ?? "").split(":").map((dir) => join(dir, "bwrap")).find((path) => existsSync(path));

const HIDDEN = [`/run/user/${process.getuid?.()}`, "/run/dbus", "/tmp/.X11-unix"].filter((path) => existsSync(path));

/** The command line that runs `command` offline, or undefined without bubblewrap. */
export function offline(command: string): string | undefined {
	if (!BWRAP) return undefined;
	const hide = HIDDEN.flatMap((path) => ["--tmpfs", path]);
	return [BWRAP, "--dev-bind", "/", "/", "--unshare-net", ...hide, "--", "bash", "-c", command].map(quote).join(" ");
}

function quote(text: string): string {
	return `'${text.replaceAll("'", `'\\''`)}'`;
}
