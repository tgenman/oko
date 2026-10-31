from oko.processing.contracts import InputSource, ProcessingRequest, processing_instruction


def test_model_request_does_not_require_settings_version():
    request = ProcessingRequest(
        operation='filter',
        sources=(InputSource(id='p',version='v1',url='https://example.test/p',title='Title',text='Body'),),
        prompt='Keep useful material',processor='llamacpp',generation=1,
    )
    assert 'Keep useful material' in processing_instruction(request)
    assert 'settings_version' not in request.model_dump()


def test_processing_input_needs_stage_signature_but_no_global_revision():
    from oko.domain.processing import ProcessingInput
    request = ProcessingRequest(
        operation='filter',
        sources=(InputSource(id='p',version='v1',url='https://example.test/p',title='Title',text='Body'),),
        prompt='Keep useful material',processor='llamacpp',generation=1,
    )
    work = ProcessingInput(item='p',kind='filter',signature='stage-signature',request=request,public_ai=False,no_input=False)
    assert work.signature == 'stage-signature'
