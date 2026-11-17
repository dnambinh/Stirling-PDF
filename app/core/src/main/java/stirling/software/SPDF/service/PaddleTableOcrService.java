package stirling.software.SPDF.service;

import java.net.URI;
import java.nio.file.Files;
import java.nio.file.StandardCopyOption;
import java.time.Duration;
import java.util.List;
import java.util.Objects;
import java.util.stream.Collectors;

import org.apache.pdfbox.pdmodel.PDDocument;
import org.springframework.core.io.FileSystemResource;
import org.springframework.http.HttpEntity;
import org.springframework.http.HttpHeaders;
import org.springframework.http.HttpMethod;
import org.springframework.http.HttpStatus;
import org.springframework.http.MediaType;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.stereotype.Service;
import org.springframework.util.LinkedMultiValueMap;
import org.springframework.util.MultiValueMap;
import org.springframework.web.client.HttpStatusCodeException;
import org.springframework.web.client.RequestCallback;
import org.springframework.web.client.ResourceAccessException;
import org.springframework.web.client.RestTemplate;
import org.springframework.web.server.ResponseStatusException;

import lombok.Getter;
import lombok.extern.slf4j.Slf4j;

import stirling.software.SPDF.model.api.PDFWithPageNums;
import stirling.software.common.model.ApplicationProperties;
import stirling.software.common.service.CustomPDFDocumentFactory;
import stirling.software.common.util.GeneralUtils;
import stirling.software.common.util.TempFile;
import stirling.software.common.util.TempFileManager;

/** Client for the internal PP-OCRv6 + PP-StructureV3 table extraction sidecar. */
@Service
@Slf4j
public class PaddleTableOcrService {

    private static final MediaType XLSX_MEDIA_TYPE =
            MediaType.parseMediaType(
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet");

    private final CustomPDFDocumentFactory pdfDocumentFactory;
    private final TempFileManager tempFileManager;
    private final ApplicationProperties.TableOcr configuration;
    private final URI extractionUri;
    private final RestTemplate restTemplate;

    public PaddleTableOcrService(
            CustomPDFDocumentFactory pdfDocumentFactory,
            TempFileManager tempFileManager,
            ApplicationProperties applicationProperties) {
        this.pdfDocumentFactory = pdfDocumentFactory;
        this.tempFileManager = tempFileManager;
        this.configuration = applicationProperties.getTableOcr();
        this.extractionUri = validateAndResolveUrl(configuration.getUrl());

        SimpleClientHttpRequestFactory requestFactory = new SimpleClientHttpRequestFactory();
        requestFactory.setConnectTimeout(
                Duration.ofSeconds(configuration.getConnectTimeoutSeconds()));
        requestFactory.setReadTimeout(Duration.ofSeconds(configuration.getTimeoutSeconds()));
        this.restTemplate = new RestTemplate(requestFactory);
    }

    public boolean isEnabled() {
        return configuration.isEnabled();
    }

    public ExtractionResult extract(PDFWithPageNums request, OutputFormat outputFormat)
            throws Exception {
        if (!isEnabled()) {
            throw new IllegalStateException("Paddle table OCR is disabled");
        }

        String originalFilename =
                Objects.requireNonNullElse(
                        request.getFileInput().getOriginalFilename(), "document.pdf");
        TempFile outputFile = tempFileManager.createManagedTempFile(".table-result");

        try (TempFile normalizedInput = tempFileManager.createManagedTempFile(".pdf")) {
            List<Integer> selectedPages = prepareSelectedPdf(request, normalizedInput);
            UpstreamMetadata metadata =
                    postForFile(
                            normalizedInput,
                            originalFilename,
                            selectedPages,
                            outputFormat,
                            outputFile);
            return new ExtractionResult(
                    outputFile,
                    sanitizeResponseFilename(metadata.filename(), originalFilename, outputFormat),
                    metadata.mediaType() != null
                            ? metadata.mediaType()
                            : outputFormat.getDefaultMediaType());
        } catch (Exception exception) {
            outputFile.close();
            throw exception;
        }
    }

    private List<Integer> prepareSelectedPdf(PDFWithPageNums request, TempFile destination)
            throws Exception {
        try (PDDocument source = pdfDocumentFactory.load(request);
                PDDocument selected = new PDDocument()) {
            List<Integer> pages = request.getPageNumbersList(source, true);
            if (pages.isEmpty()) {
                throw new ResponseStatusException(
                        HttpStatus.UNPROCESSABLE_ENTITY, "No pages were selected for table OCR");
            }
            if (pages.size() > configuration.getMaxPages()) {
                throw new ResponseStatusException(
                        HttpStatus.CONTENT_TOO_LARGE,
                        "Table OCR accepts at most " + configuration.getMaxPages() + " pages");
            }
            for (int pageNumber : pages) {
                selected.importPage(source.getPage(pageNumber - 1));
            }
            selected.save(destination.getFile());
            return pages;
        }
    }

    private UpstreamMetadata postForFile(
            TempFile inputFile,
            String originalFilename,
            List<Integer> selectedPages,
            OutputFormat outputFormat,
            TempFile outputFile) {
        MultiValueMap<String, Object> body = new LinkedMultiValueMap<>();
        body.add("file", namedPdfResource(inputFile, originalFilename));
        body.add("output_format", outputFormat.getValue());
        body.add("original_name", originalFilename);
        body.add(
                "page_labels",
                selectedPages.stream().map(String::valueOf).collect(Collectors.joining(",")));

        HttpHeaders headers = new HttpHeaders();
        headers.setContentType(MediaType.MULTIPART_FORM_DATA);
        HttpEntity<MultiValueMap<String, Object>> entity = new HttpEntity<>(body, headers);
        RequestCallback requestCallback = restTemplate.httpEntityCallback(entity);

        try {
            UpstreamMetadata metadata =
                    restTemplate.execute(
                            extractionUri,
                            HttpMethod.POST,
                            requestCallback,
                            response -> {
                                Files.copy(
                                        response.getBody(),
                                        outputFile.getPath(),
                                        StandardCopyOption.REPLACE_EXISTING);
                                return new UpstreamMetadata(
                                        response.getHeaders().getContentDisposition().getFilename(),
                                        response.getHeaders().getContentType());
                            });
            if (metadata == null) {
                throw new ResponseStatusException(
                        HttpStatus.BAD_GATEWAY, "Table OCR returned an empty response");
            }
            return metadata;
        } catch (HttpStatusCodeException exception) {
            HttpStatus status = mapUpstreamStatus(exception.getStatusCode().value());
            log.warn(
                    "Paddle table OCR rejected request with upstream status {}",
                    exception.getStatusCode().value());
            throw new ResponseStatusException(status, userFacingMessage(status), exception);
        } catch (ResourceAccessException exception) {
            if (hasCause(exception, java.net.SocketTimeoutException.class)) {
                throw new ResponseStatusException(
                        HttpStatus.GATEWAY_TIMEOUT,
                        "Table OCR timed out before processing completed",
                        exception);
            }
            throw new ResponseStatusException(
                    HttpStatus.SERVICE_UNAVAILABLE, "Table OCR service is unavailable", exception);
        }
    }

    private FileSystemResource namedPdfResource(TempFile inputFile, String originalFilename) {
        String uploadFilename =
                GeneralUtils.removeExtension(originalFilename).replaceAll("[\\\\/\r\n]", "_")
                        + ".pdf";
        return new FileSystemResource(inputFile.getFile()) {
            @Override
            public String getFilename() {
                return uploadFilename;
            }
        };
    }

    private static URI validateAndResolveUrl(String configuredUrl) {
        URI baseUri = URI.create(configuredUrl);
        String scheme = baseUri.getScheme();
        if (scheme == null
                || !(scheme.equalsIgnoreCase("http") || scheme.equalsIgnoreCase("https"))
                || baseUri.getHost() == null
                || baseUri.getUserInfo() != null
                || baseUri.getQuery() != null
                || baseUri.getFragment() != null) {
            throw new IllegalArgumentException("tableOcr.url must be a plain HTTP(S) service URL");
        }
        String base = configuredUrl.endsWith("/") ? configuredUrl : configuredUrl + "/";
        return URI.create(base).resolve("api/v1/table/extract");
    }

    private static HttpStatus mapUpstreamStatus(int upstreamStatus) {
        return switch (upstreamStatus) {
            case 413 -> HttpStatus.CONTENT_TOO_LARGE;
            case 415 -> HttpStatus.UNSUPPORTED_MEDIA_TYPE;
            case 422 -> HttpStatus.UNPROCESSABLE_ENTITY;
            case 429 -> HttpStatus.TOO_MANY_REQUESTS;
            default -> HttpStatus.BAD_GATEWAY;
        };
    }

    private static String userFacingMessage(HttpStatus status) {
        return switch (status) {
            case CONTENT_TOO_LARGE -> "PDF exceeds the table OCR service limit";
            case UNSUPPORTED_MEDIA_TYPE -> "Table OCR only accepts PDF input";
            case UNPROCESSABLE_ENTITY -> "No usable table was detected by PP-StructureV3";
            case TOO_MANY_REQUESTS -> "Table OCR queue is full; try again shortly";
            default -> "Table OCR failed to process the PDF";
        };
    }

    private static boolean hasCause(Throwable throwable, Class<? extends Throwable> causeType) {
        Throwable current = throwable;
        while (current != null) {
            if (causeType.isInstance(current)) {
                return true;
            }
            current = current.getCause();
        }
        return false;
    }

    private static String sanitizeResponseFilename(
            String upstreamFilename, String originalFilename, OutputFormat outputFormat) {
        String fallback =
                GeneralUtils.removeExtension(originalFilename)
                        + "_extracted."
                        + outputFormat.extension();
        if (upstreamFilename == null || upstreamFilename.isBlank()) {
            return fallback;
        }
        String sanitized = upstreamFilename.replaceAll("[\\\\/\r\n]", "_");
        return sanitized.length() <= 180 ? sanitized : fallback;
    }

    @Getter
    public enum OutputFormat {
        XLSX("xlsx", XLSX_MEDIA_TYPE),
        CSV("csv", MediaType.parseMediaType("text/csv; charset=utf-8"));

        private final String value;
        private final MediaType defaultMediaType;

        OutputFormat(String value, MediaType defaultMediaType) {
            this.value = value;
            this.defaultMediaType = defaultMediaType;
        }

        public String extension() {
            return value;
        }
    }

    public record ExtractionResult(TempFile tempFile, String filename, MediaType mediaType) {}

    private record UpstreamMetadata(String filename, MediaType mediaType) {}
}
