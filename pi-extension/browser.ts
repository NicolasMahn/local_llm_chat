/**
 * A browser the model can use like a person: open a page, look at it, click,
 * type, scroll and go back.
 *
 * It is the installed Chrome, headless, in a fresh profile per chat, so pages
 * see none of your cookies or logins. All its connections go through proxy.ts,
 * so pages cannot show the model what runs on this computer or the local network.
 *
 * Instead of pixel positions, each look at the page numbers its links, buttons
 * and fields, on the screenshot and in a list, and the model acts by number.
 * Models, local ones especially, name a label far more reliably than they hit
 * a coordinate.
 */

import type { Server } from "node:http";
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { type Browser, chromium, type Page } from "playwright-core";
import { Type } from "typebox";
import { startProxy } from "./proxy.ts";
import { checkPublic } from "./web.ts";

const VIEWPORT = { width: 1280, height: 900 };
const SETTLE_MS = 800; // after an action, for scripts to redraw the page
const TEXT_LIMIT = 20_000; // characters of a page browse_read returns
// The number labels drawn on the page for the model; they only appear in its screenshots.
const MARK_STYLE = "background:#e01b24;color:#fff;font:bold 12px sans-serif;padding:0 3px;border-radius:3px";

export const BROWSER_TOOLS = ["browse_open", "browse_click", "browse_type", "browse_scroll", "browse_back", "browse_read"];

type Snapshot = { title: string; url: string; marks: string[]; screenshot: string };

export class WebBrowser {
	private browser?: Browser;
	private proxy?: Server;
	private current?: Page;

	constructor(private executable: string) {}

	async page(): Promise<Page> {
		if (this.current && !this.current.isClosed()) return this.current;
		if (!this.browser) {
			const proxy = await startProxy();
			this.proxy = proxy.server;
			this.browser = await chromium.launch({
				executablePath: this.executable,
				args: [
					`--proxy-server=${proxy.address}`,
					"--proxy-bypass-list=<-loopback>", // Chrome would reach this computer directly otherwise
					"--force-webrtc-ip-handling-policy=disable_non_proxied_udp", // WebRTC does not use the proxy
					// The profile is thrown away, so it needs no keyring, which could hang or ask to unlock.
					"--password-store=basic",
				],
			});
		}
		const context = await this.browser.newContext({ viewport: VIEWPORT });
		this.current = await context.newPage();
		return this.current;
	}

	/** Ends the chat's browsing; the next look starts a fresh profile. */
	async close(): Promise<void> {
		const { browser, proxy } = this;
		this.browser = this.proxy = this.current = undefined;
		await browser?.close();
		proxy?.close();
	}

	async snapshot(page: Page): Promise<Snapshot> {
		await page.waitForLoadState("domcontentloaded").catch(() => {});
		await page.waitForTimeout(SETTLE_MS);
		const marks = await page.evaluate(markElements, MARK_STYLE);
		const screenshot = (await page.screenshot({ type: "jpeg", quality: 70 })).toString("base64");
		await page.evaluate(() => document.getElementById("llm-marks")?.remove());
		return { title: await page.title(), url: page.url(), marks, screenshot };
	}
}

/** Runs in the page: numbers what can be clicked or typed into, and lists it. */
function markElements(style: string): string[] {
	document.getElementById("llm-marks")?.remove();
	for (const old of document.querySelectorAll("[data-llm-mark]")) old.removeAttribute("data-llm-mark");
	const layer = document.createElement("div");
	layer.id = "llm-marks";
	layer.style.cssText = "position:fixed;inset:0;pointer-events:none;z-index:2147483647";
	const selector = "a[href],button,input:not([type=hidden]),textarea,select,summary,[role=button],[role=link]," +
		"[role=tab],[role=checkbox],[role=menuitem],[contenteditable=''],[contenteditable=true],[onclick]";
	const marks: string[] = [];
	for (const element of document.querySelectorAll<HTMLElement>(selector)) {
		const box = element.getBoundingClientRect();
		if (box.width < 4 || box.height < 4 || box.bottom < 0 || box.right < 0 || box.top > innerHeight || box.left > innerWidth) continue;
		// Only what is on top at its centre, not elements hidden behind others.
		const hit = document.elementFromPoint(box.left + box.width / 2, box.top + box.height / 2);
		if (!hit || !(element.contains(hit) || hit.contains(element))) continue;
		const number = marks.length + 1;
		element.setAttribute("data-llm-mark", String(number));
		const label = document.createElement("span");
		label.textContent = String(number);
		label.style.cssText = `${style};position:fixed;left:${Math.max(box.left, 0)}px;top:${Math.max(box.top, 0)}px`;
		layer.append(label);
		const tag = element.tagName.toLowerCase();
		const kind = element.getAttribute("role") ?? (tag === "a" ? "link" : tag === "input" ? `${(element as HTMLInputElement).type} field` : tag);
		const input = element as HTMLInputElement;
		const name = element.getAttribute("aria-label") || element.innerText || input.value || input.placeholder || element.title || "";
		marks.push(`[${number}] ${kind} "${name.replace(/\s+/g, " ").trim().slice(0, 80)}"`);
	}
	document.documentElement.append(layer);
	return marks;
}

/**
 * Registers the browse_* tools. `allowed` says whether this chat may go online.
 * Models that can see pictures get each look as a screenshot too; the others
 * work from the numbered list.
 */
export function registerBrowserTools(pi: ExtensionAPI, browser: WebBrowser, allowed: () => boolean) {
	async function look(page: Page, ctx: ExtensionContext, note = "") {
		const snapshot = await browser.snapshot(page);
		const elements = snapshot.marks.length ? snapshot.marks.join("\n") : "(nothing to click or type into in view)";
		const text = `${note}${snapshot.title} — ${snapshot.url}\n\nIn view, by number:\n${elements}`;
		const content: ({ type: "text"; text: string } | { type: "image"; data: string; mimeType: string })[] = [{ type: "text", text }];
		if (ctx.model?.input.includes("image")) content.push({ type: "image", data: snapshot.screenshot, mimeType: "image/jpeg" });
		// The screenshot also goes to the chat window, for you, whatever the model can see.
		return { content, details: { screenshot: snapshot.screenshot } };
	}

	async function pageFor(signal?: AbortSignal): Promise<Page> {
		if (!allowed()) throw new Error("This chat is private and has no internet access.");
		signal?.throwIfAborted();
		return browser.page();
	}

	function marked(page: Page, mark: number) {
		return page.locator(`[data-llm-mark="${mark}"]`);
	}

	// Submitting sends what the model typed somewhere; a page could have talked it into that.
	async function confirmSubmit(page: Page, ctx: ExtensionContext, what: string): Promise<boolean> {
		return ctx.ui.confirm(`Submit a form on ${new URL(page.url()).host}?`, what);
	}

	pi.registerTool({
		name: "browse_open",
		label: "Open page",
		description: "Open a web page in the browser and look at it. Returns the page's links, buttons and fields by number" +
			" (and a screenshot with those numbers, if you can see pictures). Then use browse_click, browse_type, browse_scroll.",
		promptSnippet: "Open a web page in a browser you can click and type in",
		parameters: Type.Object({ url: Type.String({ description: "The http or https address to open" }) }),
		async execute(_id, params, signal, _onUpdate, ctx) {
			const page = await pageFor(signal);
			await checkPublic(new URL(params.url));
			await page.goto(params.url, { waitUntil: "domcontentloaded", timeout: 30_000 }).catch((error: Error) => {
				// Playwright appends its call log; the first line says what went wrong.
				throw new Error(`Could not open ${params.url}: ${error.message.split("\n")[0]}`);
			});
			return look(page, ctx);
		},
	});

	pi.registerTool({
		name: "browse_click",
		label: "Click",
		description: "Click the link, button or field with this number from the last look at the page.",
		promptSnippet: "Click a numbered element in the browser",
		parameters: Type.Object({ number: Type.Integer({ description: "The element's number" }) }),
		async execute(_id, params, signal, _onUpdate, ctx) {
			const page = await pageFor(signal);
			const element = marked(page, params.number);
			const submits = await element.evaluate((el) =>
				el.matches("button[type=submit],input[type=submit],input[type=image]") ||
				(el.tagName === "BUTTON" && !el.getAttribute("type") && (el as HTMLButtonElement).form !== null),
			).catch(() => {
				throw new Error(`There is no element ${params.number}; look at the page again.`);
			});
			if (submits && !(await confirmSubmit(page, ctx, `The model wants to click "${await element.innerText()}".`))) {
				return { content: [{ type: "text", text: "The user declined to submit the form." }], details: undefined };
			}
			await element.click({ timeout: 10_000 });
			return look(page, ctx);
		},
	});

	pi.registerTool({
		name: "browse_type",
		label: "Type",
		description: "Type text into the field with this number, replacing what is in it. With submit, press Enter afterwards.",
		promptSnippet: "Type into a numbered field in the browser",
		parameters: Type.Object({
			number: Type.Integer({ description: "The field's number" }),
			text: Type.String({ description: "What to type" }),
			submit: Type.Optional(Type.Boolean({ description: "Press Enter afterwards, for example to search" })),
		}),
		async execute(_id, params, signal, _onUpdate, ctx) {
			const page = await pageFor(signal);
			const field = marked(page, params.number);
			if ((await field.getAttribute("type").catch(() => null)) === "password") {
				throw new Error("This browser does not log in anywhere; it has none of the user's accounts.");
			}
			// A search box is a form too, but asking for every search would be noise.
			const isSearch = await field.evaluate((el) =>
				(el as HTMLInputElement).type === "search" || el.getAttribute("role") === "searchbox" ||
				!!el.closest("form[role=search]") || /search|query|^q$/i.test((el as HTMLInputElement).name),
			).catch(() => false);
			if (params.submit && !isSearch && !(await confirmSubmit(page, ctx, `The model wants to enter "${params.text}" and submit.`))) {
				return { content: [{ type: "text", text: "The user declined to submit the form." }], details: undefined };
			}
			await field.fill(params.text, { timeout: 10_000 });
			if (params.submit) await field.press("Enter");
			return look(page, ctx);
		},
	});

	pi.registerTool({
		name: "browse_scroll",
		label: "Scroll",
		description: "Scroll the page down or up by about one screen.",
		promptSnippet: "Scroll the page in the browser",
		parameters: Type.Object({ direction: Type.Union([Type.Literal("down"), Type.Literal("up")]) }),
		async execute(_id, params, signal, _onUpdate, ctx) {
			const page = await pageFor(signal);
			await page.mouse.wheel(0, (params.direction === "down" ? 1 : -1) * VIEWPORT.height * 0.8);
			return look(page, ctx);
		},
	});

	pi.registerTool({
		name: "browse_back",
		label: "Back",
		description: "Go back to the previous page.",
		promptSnippet: "Go back in the browser",
		parameters: Type.Object({}),
		async execute(_id, _params, signal, _onUpdate, ctx) {
			const page = await pageFor(signal);
			await page.goBack({ waitUntil: "domcontentloaded", timeout: 30_000 });
			return look(page, ctx);
		},
	});

	pi.registerTool({
		name: "browse_read",
		label: "Read page",
		description: "Read the text of the whole current page, including what is out of view.",
		promptSnippet: "Read the text of the page open in the browser",
		parameters: Type.Object({}),
		async execute(_id, _params, signal) {
			const page = await pageFor(signal);
			const text = await page.evaluate(() => document.body?.innerText ?? "");
			const note = text.length > TEXT_LIMIT ? `\n\n[cut after ${TEXT_LIMIT} of ${text.length} characters]` : "";
			return { content: [{ type: "text", text: text.slice(0, TEXT_LIMIT) + note }], details: undefined };
		},
	});
}
