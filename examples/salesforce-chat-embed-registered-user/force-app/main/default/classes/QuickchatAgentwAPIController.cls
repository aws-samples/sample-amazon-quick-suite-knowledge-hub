public with sharing class QuickchatAgentwAPIController {
    // POST via the Named Credential Quick_Chat_Embed_API, which holds the API
    // Gateway base URL (from the CloudFormation stack) and injects the x-api-key
    // header from its External Credential. The Lambda behind it calls
    // generate_embed_url_for_registered_user and returns { "embedUrl": "..." }.
    @TestVisible
    private static final String EMBED_ENDPOINT = 'callout:Quick_Chat_Embed_API/embed-url';
    @TestVisible
    private static final String API_KEY_HEADER = 'x-api-key';
    @TestVisible
    private static final String API_KEY_MERGE_FIELD = '{!$Credential.Quick_Chat_Embed_API.ApiKey}';

    // Not cacheable: Quick embed URLs are single-use and short-lived, so every
    // load/reload must mint a fresh one.
    @AuraEnabled
    public static String getEmbedUrl() {
        HttpRequest req = new HttpRequest();
        req.setEndpoint(EMBED_ENDPOINT);
        req.setMethod('POST');
        req.setHeader('Content-Type', 'application/json');
        // API key from the External Credential principal parameter "ApiKey".
        // Salesforce resolves this merge field at send time (requires
        // allowMergeFieldsInHeader on the Named Credential); the secret never
        // appears in Apex variables or debug logs.
        req.setHeader(API_KEY_HEADER, API_KEY_MERGE_FIELD);
        req.setTimeout(120000); // embed-URL generation is quick, but give STS/describe_user headroom

        // The Lambda resolves the Quick user ARN via describe_user(UserName=...).
        // It expects the Quick registered user's UserName — typically the user's email.
        // This assumes the running Salesforce user's email matches their Quick UserName.
        Map<String, Object> payload = new Map<String, Object>{
            'userName' => UserInfo.getUserEmail()
        };
        req.setBody(JSON.serialize(payload));

        HttpResponse res;
        try {
            res = new Http().send(req);
        } catch (CalloutException e) {
            throw new AuraHandledException('Could not reach the embed URL service: ' + e.getMessage());
        }

        Integer code = res.getStatusCode();
        if (code == 200) {
            Object embedUrl;
            try {
                Map<String, Object> result =
                    (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
                embedUrl = result.get('embedUrl');
            } catch (Exception e) {
                throw new AuraHandledException('Backend returned an invalid response.');
            }
            if (embedUrl == null || String.isBlank((String) embedUrl)) {
                throw new AuraHandledException('Backend returned no embedUrl.');
            }
            return (String) embedUrl;
        }

        // The user must already exist in Amazon Quick; we never auto-register.
        if (code == 404) {
            throw new AuraHandledException(
                'You don\'t have access to the Quick chat agent yet. No Amazon Quick user is registered for '
                + UserInfo.getUserEmail() + '. Ask your administrator to add you.');
        }
        if (code == 403) {
            throw new AuraHandledException(
                'The Quick chat service rejected the request. Ask your administrator to check the API key '
                + 'on the Quick_Chat_Embed_API credential.');
        }

        // Surface the backend's JSON { "message": ... } when present.
        String msg;
        try {
            Map<String, Object> err =
                (Map<String, Object>) JSON.deserializeUntyped(res.getBody());
            msg = (String) err.get('message');
        } catch (Exception e) {
            msg = res.getBody();
        }
        throw new AuraHandledException('Embed URL request failed (HTTP ' + code + '): ' + msg);
    }
}
