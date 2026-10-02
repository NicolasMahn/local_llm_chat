/**
 * The browser's only way out: a proxy on this computer that connects to public
 * addresses only.
 *
 * Checking requests inside the browser misses redirects and some kinds of
 * connections; a proxy sees every connection, and connects to the very address
 * it checked, so a site cannot swap in a local address after the check.
 */

import { createServer, request, type Server } from "node:http";
import { connect } from "node:net";
import { publicAddress } from "./web.ts";

/** Starts the proxy on a free port of 127.0.0.1 and returns its address. */
export async function startProxy(): Promise<{ server: Server; address: string }> {
	const server = createServer(async (incoming, outgoing) => {
		// Plain http: the browser sends the whole URL.
		try {
			const url = new URL(incoming.url ?? "");
			const address = await publicAddress(url.hostname);
			const upstream = request(
				{ host: address, port: url.port || 80, method: incoming.method, path: url.pathname + url.search, headers: incoming.headers },
				(response) => {
					outgoing.writeHead(response.statusCode ?? 502, response.headers);
					response.pipe(outgoing);
				},
			);
			upstream.on("error", () => outgoing.destroy());
			incoming.pipe(upstream);
		} catch {
			outgoing.writeHead(403).end();
		}
	});
	// https and WebSockets: the browser asks for a tunnel to host:port.
	server.on("connect", async (incoming, client, head) => {
		try {
			const target = new URL(`http://${incoming.url}`);
			const address = await publicAddress(target.hostname);
			const upstream = connect(Number(target.port || 443), address, () => {
				client.write("HTTP/1.1 200 Connection Established\r\n\r\n");
				upstream.write(head);
				upstream.pipe(client);
				client.pipe(upstream);
			});
			upstream.on("error", () => client.destroy());
			client.on("error", () => upstream.destroy());
		} catch {
			client.end("HTTP/1.1 403 Forbidden\r\n\r\n");
		}
	});
	await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
	const { port } = server.address() as { port: number };
	return { server, address: `http://127.0.0.1:${port}` };
}
