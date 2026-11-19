import { LightningElement, api } from "lwc";
import { loadScript } from "lightning/platformResourceLoader";
import QUICKSIGHT_SDK from "@salesforce/resourceUrl/QuickChatEmbeddingSDK";
import getEmbedUrl from "@salesforce/apex/QuickchatAgentwAPIController.getEmbedUrl";

const DEFAULT_HEIGHT = 600;

/**
 * Embeds an Amazon Quick chat agent using the amazon-quicksight-embedding-sdk.
 * Flow: load SDK static resource -> Apex mints a single-use embed URL ->
 * createEmbeddingContext() -> embedQuickChat() into a manual-DOM container.
 */
export default class QuickchatAgentwAPI extends LightningElement {
  /** Card header text. */
  @api cardTitle = "Quick Chat Agent";
  /** Chat height in pixels. */
  @api height = DEFAULT_HEIGHT;
  /** Optional: lock the chat to a specific Quick agent ID. */
  @api agentId;
  /** Optional: chat locale, e.g. en-US or ja-JP. Unsupported values fall back to en-US. */
  @api locale;
  /** Show the attach-files button in the prompt. */
  @api allowFileAttachments = false;

  isLoading = true;
  errorMessage;

  _initialized = false;
  _sdkLoaded = false;
  _embeddingContext;

  get containerStyle() {
    const h = Number.parseInt(this.height, 10);
    return `height: ${Number.isFinite(h) && h > 0 ? h : DEFAULT_HEIGHT}px;`;
  }

  renderedCallback() {
    // The container div must exist before we embed, so start here (once).
    if (this._initialized) {
      return;
    }
    this._initialized = true;
    this.embedChat();
  }

  handleReload() {
    // Embed URLs are single-use, so a reload always mints a new one.
    this.embedChat();
  }

  async embedChat() {
    this.isLoading = true;
    this.errorMessage = undefined;

    try {
      const sdk = await this.loadSdk();
      const embedUrl = await getEmbedUrl();

      const container = this.refs.container;
      // Drop any previous iframe on reload. replaceChildren() isn't available
      // on Salesforce's secure DOM wrappers, so use removeChild.
      while (container.firstChild) {
        container.removeChild(container.firstChild);
      }

      if (!this._embeddingContext) {
        this._embeddingContext = await sdk.createEmbeddingContext();
      }

      await this._embeddingContext.embedQuickChat(
        {
          url: embedUrl,
          container,
          width: "100%",
          height: "100%",
          framePermissions: { clipboardWrite: true },
          onChange: (changeEvent) => this.handleFrameChange(changeEvent),
        },
        this.buildContentOptions(),
      );
    } catch (error) {
      this.showError(error);
    }
  }

  async loadSdk() {
    if (!this._sdkLoaded) {
      await loadScript(this, QUICKSIGHT_SDK);
      this._sdkLoaded = true;
    }
    // The UMD bundle registers itself as a global.
    const sdk = window.QuickSightEmbedding;
    if (!sdk || typeof sdk.createEmbeddingContext !== "function") {
      throw new Error("Amazon Quick embedding SDK did not initialize.");
    }
    return sdk;
  }

  buildContentOptions() {
    const options = {
      promptOptions: { allowFileAttachments: this.allowFileAttachments === true },
    };
    // When set, the SDK opens the chat on this agent and locks it there.
    const agentId = (this.agentId || "").trim();
    if (agentId) {
      options.agentOptions = { fixedAgentId: agentId };
    }
    if (this.locale) {
      options.locale = this.locale;
    }
    return options;
  }

  handleFrameChange(changeEvent) {
    if (!changeEvent) {
      return;
    }
    if (changeEvent.eventLevel === "ERROR") {
      this.showError(new Error(changeEvent.message || changeEvent.eventName));
    } else if (changeEvent.eventName === "FRAME_LOADED") {
      this.isLoading = false;
    }
  }

  showError(error) {
    this.isLoading = false;
    this.errorMessage = reduceError(error);
    console.error("Quick chat embed failed", error);
  }
}

function reduceError(error) {
  if (!error) {
    return "Unknown error";
  }
  // Apex AuraHandledException
  if (error.body) {
    if (Array.isArray(error.body)) {
      return error.body.map((e) => e.message).join(", ");
    }
    if (typeof error.body.message === "string") {
      return error.body.message;
    }
  }
  if (typeof error.message === "string") {
    return error.message;
  }
  return typeof error === "string" ? error : JSON.stringify(error);
}
