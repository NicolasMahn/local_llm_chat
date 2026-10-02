/**
 * What the web tools may reach: public http(s) addresses only.
 *
 * Pages go to the model, and in a normal chat the model may be a hosted one, so
 * a tool must not open what runs on this computer or the local network (the
 * router, the model gateway, other services).
 */

import { lookup } from "node:dns/promises";
import { BlockList, isIP } from "node:net";

const LOCAL_NETWORKS = new BlockList();
for (const [network, prefix] of [
	["0.0.0.0", 8], ["10.0.0.0", 8], ["100.64.0.0", 10], ["127.0.0.0", 8], ["169.254.0.0", 16],
	["172.16.0.0", 12], ["192.168.0.0", 16], ["224.0.0.0", 3],
] as const) LOCAL_NETWORKS.addSubnet(network, prefix, "ipv4");
for (const [network, prefix] of [["::", 127], ["fc00::", 7], ["fe80::", 10], ["ff00::", 8]] as const) {
	LOCAL_NETWORKS.addSubnet(network, prefix, "ipv6");
}

const MAX_REDIRECTS = 5;
const USER_AGENT = "Mozilla/5.0 (local-llm-chat)";

/** Throws unless the URL is http(s) and every address its host resolves to is public. */
export async function checkPublic(url: URL): Promise<void> {
	if (url.protocol !== "http:" && url.protocol !== "https:") {
		throw new Error(`Only http and https addresses can be opened, not ${url.protocol}`);
	}
	await publicAddress(url.hostname);
}

/** An address of `host` to connect to; throws if any of its addresses is local. */
export async function publicAddress(host: string): Promise<string> {
	const name = host.replace(/^\[|\]$/g, "");
	const addresses = isIP(name) ? [{ address: name, family: isIP(name) }] : await lookup(name, { all: true });
	if (addresses.length === 0 || addresses.some(({ address, family }) => isLocal(address, family))) {
		throw new Error(`${host} is on this computer or the local network, which the web tools may not open.`);
	}
	return addresses[0].address;
}

function isLocal(address: string, family: number): boolean {
	const mapped = /^::ffff:(\d+\.\d+\.\d+\.\d+)$/i.exec(address); // an IPv4 address written as IPv6
	if (mapped) return LOCAL_NETWORKS.check(mapped[1], "ipv4");
	return LOCAL_NETWORKS.check(address, family === 6 ? "ipv6" : "ipv4");
}

/**
 * The text of a public page, reduced from HTML, and at most `maxBytes` of it read.
 * Redirects are followed one at a time, so each new address is checked too.
 */
export async function fetchPage(href: string, maxBytes: number, signal?: AbortSignal): Promise<string> {
	let url = new URL(href);
	for (let hop = 0; hop <= MAX_REDIRECTS; hop++) {
		await checkPublic(url);
		const response = await fetch(url, { redirect: "manual", headers: { "user-agent": USER_AGENT }, signal });
		const location = response.headers.get("location");
		if (response.status >= 300 && response.status < 400 && location) {
			await response.body?.cancel();
			url = new URL(location, url);
			continue;
		}
		if (!response.ok) throw new Error(`HTTP ${response.status} from ${url.href}`);
		const body = await readUpTo(response, maxBytes);
		return (response.headers.get("content-type") ?? "").includes("html") ? htmlToText(body) : body;
	}
	throw new Error(`More than ${MAX_REDIRECTS} redirects from ${href}`);
}

// A huge page or file would otherwise be read whole before being cut down.
async function readUpTo(response: Response, maxBytes: number): Promise<string> {
	if (!response.body) return "";
	const chunks: Uint8Array[] = [];
	let size = 0;
	for await (const chunk of response.body) {
		chunks.push(chunk);
		size += chunk.length;
		if (size >= maxBytes) break; // leaving the loop cancels the rest of the download
	}
	return new TextDecoder().decode(Buffer.concat(chunks).subarray(0, maxBytes));
}

function htmlToText(html: string): string {
	return html
		.replace(/<(script|style|noscript|svg|head)[\s\S]*?<\/\1>/gi, "")
		.replace(/<(br|\/p|\/div|\/li|\/h[1-6]|\/tr)[^>]*>/gi, "\n")
		.replace(/<[^>]+>/g, "")
		.replace(/&nbsp;/g, " ")
		.replace(/&amp;/g, "&")
		.replace(/&lt;/g, "<")
		.replace(/&gt;/g, ">")
		.replace(/&quot;/g, '"')
		.replace(/&#39;/g, "'")
		.replace(/[ \t]+/g, " ")
		.replace(/\n\s*\n+/g, "\n\n")
		.trim();
}
