package keel.api.common

import org.slf4j.LoggerFactory
import org.springframework.http.HttpStatus
import org.springframework.http.ResponseEntity
import org.springframework.http.converter.HttpMessageNotReadableException
import org.springframework.web.HttpMediaTypeNotSupportedException
import org.springframework.web.HttpRequestMethodNotSupportedException
import org.springframework.web.bind.MethodArgumentNotValidException
import org.springframework.web.bind.MissingServletRequestParameterException
import org.springframework.web.bind.annotation.ExceptionHandler
import org.springframework.web.bind.annotation.RestControllerAdvice
import org.springframework.web.context.request.async.AsyncRequestNotUsableException
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException
import org.springframework.web.servlet.resource.NoResourceFoundException

/** The one error shape every route returns: `{ error, hint? }`. */
data class ErrorBody(val error: String, val hint: String? = null)

open class ApiException(
    val status: HttpStatus,
    override val message: String,
    val hint: String? = null,
) : RuntimeException(message)

class NotFound(what: String, hint: String? = null) : ApiException(HttpStatus.NOT_FOUND, what, hint)
class BadRequest(what: String, hint: String? = null) : ApiException(HttpStatus.BAD_REQUEST, what, hint)
class Conflict(what: String, hint: String? = null) : ApiException(HttpStatus.CONFLICT, what, hint)
class Forbidden(what: String, hint: String? = null) : ApiException(HttpStatus.FORBIDDEN, what, hint)

@RestControllerAdvice
class ErrorAdvice {
    private val log = LoggerFactory.getLogger(javaClass)

    @ExceptionHandler(ApiException::class)
    fun api(e: ApiException): ResponseEntity<ErrorBody> =
        ResponseEntity.status(e.status).body(ErrorBody(e.message, e.hint))

    @ExceptionHandler(MethodArgumentNotValidException::class)
    fun invalid(e: MethodArgumentNotValidException): ResponseEntity<ErrorBody> {
        val fields = e.bindingResult.fieldErrors.joinToString(", ") { "${it.field} ${it.defaultMessage}" }
        return ResponseEntity.badRequest().body(ErrorBody("Some fields are not valid", fields))
    }

    @ExceptionHandler(HttpMessageNotReadableException::class)
    fun unreadable(e: HttpMessageNotReadableException): ResponseEntity<ErrorBody> =
        ResponseEntity.badRequest().body(ErrorBody("The request body is not valid JSON for this route", e.mostSpecificCause.message?.take(300)))

    @ExceptionHandler(MissingServletRequestParameterException::class, MethodArgumentTypeMismatchException::class)
    fun badParam(e: Exception): ResponseEntity<ErrorBody> =
        ResponseEntity.badRequest().body(ErrorBody("A query parameter is missing or wrong", e.message))

    @ExceptionHandler(NoResourceFoundException::class)
    fun noResource(e: NoResourceFoundException): ResponseEntity<ErrorBody> =
        ResponseEntity.status(HttpStatus.NOT_FOUND).body(ErrorBody("Not found", "/${e.resourcePath}"))

    @ExceptionHandler(HttpRequestMethodNotSupportedException::class)
    fun method(e: HttpRequestMethodNotSupportedException): ResponseEntity<ErrorBody> =
        ResponseEntity.status(HttpStatus.METHOD_NOT_ALLOWED).body(ErrorBody("This method is not allowed here", e.message))

    @ExceptionHandler(HttpMediaTypeNotSupportedException::class)
    fun media(e: HttpMediaTypeNotSupportedException): ResponseEntity<ErrorBody> =
        ResponseEntity.status(HttpStatus.UNSUPPORTED_MEDIA_TYPE).body(ErrorBody("Send JSON", e.message))

    @ExceptionHandler(AsyncRequestNotUsableException::class)
    fun gone(e: AsyncRequestNotUsableException) {
        // An SSE client went away. Nothing to answer.
    }

    @ExceptionHandler(Exception::class)
    fun other(e: Exception): ResponseEntity<ErrorBody> {
        log.error("unhandled error", e)
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
            .body(ErrorBody("Something went wrong in the api", e.message?.take(300)))
    }
}
