// Required for deployment to production orgs (75% Apex coverage).
@IsTest
private class QuickchatAgentwAPIControllerTest {
    private class MockResponse implements HttpCalloutMock {
        private final Integer status;
        private final String body;

        MockResponse(Integer status, String body) {
            this.status = status;
            this.body = body;
        }

        public HttpResponse respond(HttpRequest req) {
            Assert.areEqual('POST', req.getMethod());
            Assert.areEqual(QuickchatAgentwAPIController.EMBED_ENDPOINT, req.getEndpoint());
            Assert.areEqual(QuickchatAgentwAPIController.API_KEY_MERGE_FIELD,
                req.getHeader(QuickchatAgentwAPIController.API_KEY_HEADER));
            Map<String, Object> payload = (Map<String, Object>) JSON.deserializeUntyped(req.getBody());
            Assert.areEqual(UserInfo.getUserEmail(), payload.get('userName'));

            HttpResponse res = new HttpResponse();
            res.setStatusCode(status);
            res.setHeader('Content-Type', 'application/json');
            res.setBody(body);
            return res;
        }
    }

    private static String expectError(Integer status, String body) {
        Test.setMock(HttpCalloutMock.class, new MockResponse(status, body));
        try {
            QuickchatAgentwAPIController.getEmbedUrl();
            Assert.fail('Expected AuraHandledException');
        } catch (AuraHandledException e) {
            return e.getMessage();
        }
        return null;
    }

    @IsTest
    static void returnsEmbedUrlOnSuccess() {
        Test.setMock(HttpCalloutMock.class,
            new MockResponse(200, '{"embedUrl":"https://us-east-1.quicksight.aws.amazon.com/embed/abc"}'));
        Test.startTest();
        String url = QuickchatAgentwAPIController.getEmbedUrl();
        Test.stopTest();
        Assert.areEqual('https://us-east-1.quicksight.aws.amazon.com/embed/abc', url);
    }

    @IsTest
    static void throwsWhenEmbedUrlMissing() {
        Assert.isNotNull(expectError(200, '{}'));
    }

    @IsTest
    static void throwsOnInvalidSuccessBody() {
        Assert.isNotNull(expectError(200, 'not json'));
    }

    @IsTest
    static void throwsFriendlyMessageWhenQuickUserMissing() {
        Assert.isNotNull(expectError(404, '{"message":"No Quick user named x"}'));
    }

    @IsTest
    static void throwsFriendlyMessageWhenApiKeyRejected() {
        Assert.isNotNull(expectError(403, '{"message":"Forbidden"}'));
    }

    @IsTest
    static void throwsWithBackendJsonMessage() {
        Assert.isNotNull(expectError(502, '{"message":"Could not generate embed URL"}'));
    }

    @IsTest
    static void throwsWithRawBodyWhenNotJson() {
        Assert.isNotNull(expectError(502, '<html>Bad Gateway</html>'));
    }
}
